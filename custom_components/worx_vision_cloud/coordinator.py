"""Coordinator for Worx Vision Cloud Plus."""
from __future__ import annotations

import asyncio
from collections import deque
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, time, timedelta
import json
import logging
from typing import Any

from aiohttp import ClientError, ClientTimeout

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, HomeAssistantError
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from pyworxcloud import DeviceHandler, LandroidEvent, WorxCloud
from pyworxcloud.exceptions import (
    AuthorizationError,
    NoConnectionError,
    TimeoutException,
    TooManyRequestsError,
)
from pyworxcloud.utils.requests import AGET, HEADERS

from .const import (
    DOMAIN,
    MAINTENANCE_BATTERY_CYCLES,
    MAINTENANCE_BLADE_DAYS,
    MAINTENANCE_BLADE_HOURS,
    MAINTENANCE_DEFAULTS,
    MAINTENANCE_LIMITS,
)
from .helpers import (
    MOWING_STATUS_IDS,
    get_dict_value,
    maintenance_summary,
    rtk_position,
)
from .statistics import DailyStatisticsTracker

_LOGGER = logging.getLogger(__name__)

RTK_MAP_CACHE_TTL = timedelta(minutes=30)
RTK_ADDRESS_CACHE_TTL = timedelta(hours=24)
# Four decimal places is roughly 11 m: precise enough for a street address, but it
# does not reveal the mower's exact position and keeps the cache stable while mowing.
RTK_ADDRESS_COORD_PRECISION = 4
RTK_ADDRESS_ENDPOINT = "https://nominatim.openstreetmap.org/reverse"
RTK_ADDRESS_USER_AGENT = (
    "Worx Vision Cloud PLUS Home Assistant custom integration "
    "(https://github.com/SmartServicePL/worx_vision_cloud_plus_github)"
)
PRODUCT_ITEM_CACHE_TTL = timedelta(minutes=5)
FIRMWARE_UPGRADE_CACHE_TTL = timedelta(minutes=30)
STATISTICS_STORAGE_VERSION = 1
STATISTICS_SAVE_DELAY = 60
OPTIONS_STORAGE_VERSION = 1
OPTIONS_SAVE_DELAY = 5
MAINTENANCE_STORAGE_VERSION = 1
REST_RATE_LIMIT_BACKOFF = timedelta(hours=1)
POST_COMMAND_REFRESH_TIMEOUT = 10
# Private pyworxcloud attributes used for the REST statistics, map and firmware
# requests. They are checked once at setup so a library change is visible.
PRIVATE_CLOUD_ATTRIBUTES = ("_api",)
RTK_TRAIL_MAX_POINTS = 300
DEFAULT_ONE_TIME_MOWING_RUNTIME = 60
DEFAULT_ONE_TIME_MOWING_EDGE_CUT = False


def _device_map(cloud: WorxCloud) -> dict[str, DeviceHandler]:
    """Build a serial-number-indexed map of devices from pyworxcloud."""
    devices: dict[str, DeviceHandler] = {}
    for device in cloud.devices.values():
        serial = getattr(device, "serial_number", None)
        if serial is not None:
            devices[str(serial)] = device
    return devices


def _normalize_zone_ids(zones: list[int] | None) -> list[int]:
    """Return ordered, de-duplicated positive zone identifiers."""
    normalized: list[int] = []
    for zone in zones or []:
        zone_id = int(zone)
        if zone_id > 0 and zone_id not in normalized:
            normalized.append(zone_id)
    return normalized


class WorxVisionCoordinator(DataUpdateCoordinator[dict[str, DeviceHandler]]):
    """Coordinate push and manual updates."""

    def __init__(
        self, hass: HomeAssistant, cloud: WorxCloud, config_entry: ConfigEntry
    ) -> None:
        """Initialize coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=config_entry,
            name=DOMAIN,
            update_interval=PRODUCT_ITEM_CACHE_TTL,
            always_update=True,
        )
        self.cloud = cloud
        self._statistics_store = Store[dict[str, Any]](
            hass,
            STATISTICS_STORAGE_VERSION,
            f"{DOMAIN}.{config_entry.entry_id}.statistics",
        )
        self._statistics = DailyStatisticsTracker()
        self._statistics_save_pending = False
        self._options_store = Store[dict[str, Any]](
            hass,
            OPTIONS_STORAGE_VERSION,
            f"{DOMAIN}.{config_entry.entry_id}.one_time_mowing",
        )
        self._shutdown_complete = False
        self._event_lock = asyncio.Lock()
        self._rtk_address_lock = asyncio.Lock()
        self._last_rtk_address_lookup: datetime | None = None
        self._rtk_map_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._rtk_address_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._product_item_cache: dict[str, tuple[datetime, dict[str, Any]]] = {}
        self._firmware_upgrade_cache: dict[
            str, tuple[datetime, dict[str, Any]]
        ] = {}
        self._rtk_position_trails: dict[
            str, deque[tuple[datetime, float, float]]
        ] = {}
        self._one_time_mowing_options: dict[str, dict[str, Any]] = {}
        self._maintenance_store = Store[dict[str, Any]](
            hass,
            MAINTENANCE_STORAGE_VERSION,
            f"{DOMAIN}.{config_entry.entry_id}.maintenance",
        )
        self._maintenance: dict[str, dict[str, int]] = {}
        self._connect_lock = asyncio.Lock()
        self._connect_generation = 0
        self._rest_backoff_until: datetime | None = None
        self._rest_auth_error: AuthorizationError | None = None

    async def async_setup(self) -> None:
        """Attach pyworxcloud callbacks."""
        self._statistics = DailyStatisticsTracker(
            await self._statistics_store.async_load()
        )
        self._restore_one_time_options(await self._options_store.async_load())
        self._restore_maintenance(await self._maintenance_store.async_load())
        self._check_private_cloud_api()

        def _on_data_received(name: str, device: DeviceHandler) -> None:
            del name
            self._schedule_push_update(device)

        def _on_api_update(api_data: dict[str, Any], **_: Any) -> None:
            del api_data
            self._schedule_api_refresh()

        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, _on_data_received)
        self.cloud.set_callback(LandroidEvent.API, _on_api_update)

    async def async_shutdown(self) -> None:
        """Detach callbacks."""
        if self._shutdown_complete:
            return
        self._shutdown_complete = True
        self.cloud.set_callback(LandroidEvent.DATA_RECEIVED, lambda *_, **__: None)
        self.cloud.set_callback(LandroidEvent.API, lambda *_, **__: None)
        self._statistics_save_pending = False
        try:
            await self._statistics_store.async_save(self._statistics.as_dict())
            await self._options_store.async_save(self._one_time_mowing_options)
            await self._maintenance_store.async_save(self._maintenance)
        finally:
            await super().async_shutdown()

    async def _handle_push_update(self, device: DeviceHandler) -> None:
        """Merge one pushed device update."""
        serial = getattr(device, "serial_number", None)
        if serial is None:
            return

        async with self._event_lock:
            self._preserve_enriched_attributes(str(serial), device)
            self._remember_rtk_position(str(serial), device)
            self._update_daily_statistics(str(serial), device)
            data = dict(self.data or {})
            data[str(serial)] = device
            self._async_publish_data(data)

    def _async_publish_data(self, data: dict[str, DeviceHandler]) -> None:
        """Notify entities without postponing the periodic REST refresh.

        MQTT pushes can arrive more often than the refresh interval. Using
        async_set_updated_data here would restart the refresh timer on every push
        and starve the product-item statistics while the mower is working.
        """
        self.data = data
        # A pushed MQTT update proves that the cloud connection works again.
        self.last_update_success = True
        self.async_update_listeners()

    async def _refresh_from_cloud_cache(self) -> dict[str, DeviceHandler]:
        """Return current cloud cache."""
        devices = _device_map(self.cloud)
        results = await asyncio.gather(
            *(self._enrich_device(serial, device) for serial, device in devices.items()),
            return_exceptions=True,
        )
        for serial_number, result in zip(devices, results):
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Could not enrich mower %s with Worx REST data: %s",
                    serial_number,
                    result,
                )
        return devices

    async def _async_update_data(self) -> dict[str, DeviceHandler]:
        """Return current cloud cache for DataUpdateCoordinator.

        A lost cloud connection that cannot be re-established raises
        UpdateFailed, so entities become unavailable instead of showing stale
        state. A rejected login raises ConfigEntryAuthFailed (re-authentication).
        """
        await self._async_check_cloud_connection()
        self._rest_auth_error = None
        try:
            devices = await self._refresh_from_cloud_cache()
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Could not read Worx cloud data: {err}") from err
        if self._rest_auth_error is not None:
            raise ConfigEntryAuthFailed(
                "Worx rejected the saved login"
            ) from self._rest_auth_error
        return devices

    async def _async_check_cloud_connection(self) -> None:
        """Re-establish a dropped Worx MQTT connection during a periodic refresh."""
        if getattr(self.cloud, "mqtt_connected", None) is not False:
            return

        _LOGGER.info("Worx MQTT connection is down; reconnecting")
        try:
            await self._async_reconnect()
        except AuthorizationError as err:
            raise ConfigEntryAuthFailed("Worx rejected the saved login") from err
        except TooManyRequestsError as err:
            raise UpdateFailed("Worx cloud rate limit while reconnecting") from err
        except Exception as err:  # noqa: BLE001
            raise UpdateFailed(f"Could not reconnect to Worx cloud: {err}") from err
        if getattr(self.cloud, "mqtt_connected", None) is False:
            raise UpdateFailed("Worx MQTT connection could not be re-established")

    async def async_request_device_update(self, serial_number: str) -> None:
        """Ask one mower for a fresh MQTT state update, then refresh coordinator data."""
        try:
            await self.cloud.update(serial_number)
        finally:
            await self.async_request_refresh()

    async def async_refresh_device(self, serial_number: str) -> None:
        """Request a fresh mower state on user request (refresh button)."""
        await self._async_run_cloud_call(
            lambda: self.cloud.update(serial_number), ensure_mqtt=True
        )
        await self.async_request_refresh()

    async def _async_reconnect(self, *, generation: int | None = None) -> None:
        """Reconnect the Worx cloud session, one caller at a time.

        Without a generation the reconnect is skipped when MQTT is already
        connected. With a generation (read before a failed publish) it is
        skipped when another caller has reconnected in the meantime.
        """
        async with self._connect_lock:
            if generation is None:
                if getattr(self.cloud, "mqtt_connected", False):
                    return
            elif generation != self._connect_generation:
                return
            await self.cloud.connect()
            self._connect_generation += 1

    async def _async_ensure_mqtt_connected(self) -> None:
        """Reconnect MQTT before sending mower commands when the cloud session is stale."""
        if getattr(self.cloud, "mqtt_connected", False):
            return

        _LOGGER.warning("Worx MQTT connection is not ready; reconnecting before command")
        await self._async_reconnect()
        if not getattr(self.cloud, "mqtt_connected", False):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="mqtt_not_ready"
            )

    async def _async_publish_command(
        self, serial_number: str, topic: str, message: dict[str, Any], protocol: int
    ) -> None:
        """Publish a mower command, reconnecting MQTT once if needed."""
        await self._async_ensure_mqtt_connected()
        mqtt = getattr(self.cloud, "mqtt", None)
        if mqtt is None:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="mqtt_not_ready"
            )

        generation = self._connect_generation
        try:
            await mqtt.apublish(serial_number, topic, message, protocol)
        except NoConnectionError:
            _LOGGER.warning("Worx MQTT command failed because connection was stale; retrying once")
            await self._async_reconnect(generation=generation)
            mqtt = getattr(self.cloud, "mqtt", None)
            if mqtt is None:
                raise HomeAssistantError(
                    translation_domain=DOMAIN, translation_key="mqtt_not_ready"
                ) from None
            await mqtt.apublish(serial_number, topic, message, protocol)

    async def _async_run_cloud_call(
        self,
        call: Callable[[], Awaitable[Any]],
        *,
        ensure_mqtt: bool = False,
        retry_on_disconnect: bool = True,
    ) -> Any:
        """Run one Worx cloud call and translate library errors.

        Every failure leaves as HomeAssistantError, so automations and scripts
        can handle it (for example with continue_on_error). A rejected login
        also starts re-authentication. A call that failed because the MQTT
        connection was down did not reach the mower, so it is retried once
        after reconnecting.
        """
        try:
            if ensure_mqtt:
                await self._async_ensure_mqtt_connected()
            generation = self._connect_generation
            try:
                return await call()
            except NoConnectionError:
                if not retry_on_disconnect:
                    raise
                _LOGGER.warning(
                    "Worx command failed because the connection was down; "
                    "reconnecting and retrying once"
                )
                await self._async_reconnect(generation=generation)
                return await call()
        except HomeAssistantError:
            raise
        except AuthorizationError as err:
            if self.config_entry is not None:
                self.config_entry.async_start_reauth(self.hass)
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cloud_auth_failed"
            ) from err
        except TooManyRequestsError as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cloud_rate_limited"
            ) from err
        except (TimeoutException, TimeoutError) as err:
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="cloud_command_timeout"
            ) from err
        except Exception as err:  # noqa: BLE001 - the library raises many types
            raise HomeAssistantError(
                translation_domain=DOMAIN,
                translation_key="cloud_command_failed",
                translation_placeholders={"error": str(err) or type(err).__name__},
            ) from err

    async def async_cloud_command(
        self,
        serial_number: str,
        call: Callable[[], Awaitable[Any]],
        *,
        ensure_mqtt: bool = False,
    ) -> None:
        """Send one mower command, then refresh its state on a best-effort basis.

        Only the command itself can fail the action: a command that reached
        the cloud is not reported as failed because the following state refresh
        failed.
        """
        await self._async_run_cloud_call(call, ensure_mqtt=ensure_mqtt)
        await self._async_request_device_update_best_effort(serial_number)

    async def _async_request_device_update_best_effort(self, serial_number: str) -> None:
        """Refresh device state after a command without failing an accepted command."""
        try:
            await asyncio.wait_for(
                self.async_request_device_update(serial_number),
                timeout=POST_COMMAND_REFRESH_TIMEOUT,
            )
        except (NoConnectionError, TimeoutException, TimeoutError) as err:
            _LOGGER.warning(
                "Worx command was sent, but state refresh did not finish: %s", err
            )
        except Exception as err:  # noqa: BLE001 - command already succeeded
            _LOGGER.debug(
                "Worx command was sent, but best-effort state refresh failed",
                exc_info=True,
            )

    async def async_start_edge_cut(self, serial_number: str) -> None:
        """Start an on-demand edge cutting task."""
        await self._async_run_cloud_call(
            lambda: self._async_send_edge_cut(serial_number),
            retry_on_disconnect=False,
        )
        await self._async_request_device_update_best_effort(serial_number)

    async def _async_send_edge_cut(self, serial_number: str) -> None:
        """Publish the edge cutting command for the mower protocol."""
        mower = self.cloud.get_mower(serial_number)
        if not mower.get("online"):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="mower_offline"
            )

        protocol = mower.get("protocol")
        command_topic = (mower.get("mqtt_topics") or {}).get("command_in")
        if command_topic is None:
            raise HomeAssistantError("Worx command topic is not available")

        # On Vision Cloud firmware 3.46.x cmd 101 is the reliable edge-only
        # command. Full one-time mowing uses cmd 10 instead.
        if protocol == 0:
            await self._async_publish_command(
                serial_number,
                command_topic,
                {"sc": {"ots": {"bc": 1, "wtm": 0}}},
                protocol,
            )
        elif protocol == 1:
            await self._async_send_one_time_mowing(serial_number, 0, True, [])
        else:
            raise HomeAssistantError(
                "Edge cutting is not supported for this mower protocol"
            )

    async def async_start_one_time_mowing(
        self,
        serial_number: str,
        runtime_minutes: int,
        edge_cut: bool = False,
        zones: list[int] | None = None,
    ) -> None:
        """Start a one-time mowing task, optionally limited to RTK zones.

        Cloud errors are raised as HomeAssistantError. A command that reached
        the cloud is not reported as failed when the following refresh fails.
        """
        await self._async_run_cloud_call(
            lambda: self._async_send_one_time_mowing(
                serial_number, runtime_minutes, edge_cut, zones
            ),
            retry_on_disconnect=False,
        )
        await self._async_request_device_update_best_effort(serial_number)

    async def _async_send_one_time_mowing(
        self,
        serial_number: str,
        runtime_minutes: int,
        edge_cut: bool = False,
        zones: list[int] | None = None,
    ) -> None:
        """Publish the one-time mowing command for the mower protocol."""
        mower = self.cloud.get_mower(serial_number)
        if not mower.get("online"):
            raise HomeAssistantError(
                translation_domain=DOMAIN, translation_key="mower_offline"
            )

        protocol = mower.get("protocol")
        runtime = int(runtime_minutes)
        zone_ids = _normalize_zone_ids(zones)
        if runtime <= 0 and not edge_cut:
            raise HomeAssistantError(
                "Runtime must be at least 1 minute unless only edge cutting is requested"
            )
        if protocol == 0:
            command_topic = (mower.get("mqtt_topics") or {}).get("command_in")
            if command_topic is None:
                raise HomeAssistantError("Worx command topic is not available")

            if len(zone_ids) > 1:
                raise HomeAssistantError(
                    "Legacy Worx protocol supports only one selected zone per one-time mowing command"
                )

            setzone = getattr(self.cloud, "setzone", None)
            if zone_ids and setzone is not None:
                await setzone(serial_number, zone_ids[0])

            await self._async_publish_command(
                serial_number,
                command_topic,
                {"sc": {"ots": {"bc": int(edge_cut), "wtm": runtime}}},
                protocol,
            )
        elif protocol == 1:
            command_topic = (mower.get("mqtt_topics") or {}).get("command_in")
            if command_topic is None:
                raise HomeAssistantError("Worx command topic is not available")

            uuid = mower.get("uuid")
            if uuid is None:
                raise HomeAssistantError("Worx mower UUID is not available")

            if edge_cut and runtime == 0 and not zone_ids:
                await self._async_publish_command(
                    uuid, command_topic, {"cmd": 101}, protocol
                )
            else:
                if zone_ids:
                    # Reported for WR312E firmware 3.46.x (issue #5): the
                    # mower accepts this command but ignores the zone list.
                    # The mower data does not tell which firmware honours it,
                    # so the command is still sent and the risk is logged.
                    _LOGGER.warning(
                        "Mower %s uses the Vision/RTK protocol; its firmware may "
                        "ignore the selected zones %s and mow all zones",
                        serial_number,
                        zone_ids,
                    )
                await self._async_publish_command(
                    uuid,
                    command_topic,
                    {
                        "cmd": 10,
                        "sc": {
                            "once": {
                                "time": runtime,
                                "cfg": {"cut": {"b": int(edge_cut), "z": zone_ids}},
                            }
                        },
                    },
                    protocol,
                )
        else:
            raise HomeAssistantError(
                "One-time mowing is not supported for this mower protocol"
            )

    def _one_time_options(self, serial_number: str) -> dict[str, Any]:
        """Return local one-time mowing options for a mower."""
        return self._one_time_mowing_options.setdefault(
            serial_number,
            {
                "runtime": DEFAULT_ONE_TIME_MOWING_RUNTIME,
                "edge_cut": DEFAULT_ONE_TIME_MOWING_EDGE_CUT,
                "zones": [],
            },
        )

    def one_time_mowing_runtime(self, serial_number: str) -> int:
        """Return configured one-time mowing runtime."""
        return int(
            self._one_time_options(serial_number).get(
                "runtime", DEFAULT_ONE_TIME_MOWING_RUNTIME
            )
        )

    def one_time_mowing_edge_cut(self, serial_number: str) -> bool:
        """Return whether configured one-time mowing starts with edge cutting."""
        return bool(
            self._one_time_options(serial_number).get(
                "edge_cut", DEFAULT_ONE_TIME_MOWING_EDGE_CUT
            )
        )

    def one_time_mowing_zones(self, serial_number: str) -> list[int]:
        """Return configured one-time mowing RTK zones."""
        return list(self._one_time_options(serial_number).get("zones", []))

    async def async_set_one_time_mowing_runtime(
        self, serial_number: str, runtime_minutes: int
    ) -> None:
        """Set local one-time mowing runtime."""
        runtime = max(10, min(120, int(runtime_minutes)))
        self._one_time_options(serial_number)["runtime"] = runtime
        self._async_options_changed()

    async def async_set_one_time_mowing_edge_cut(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Set whether local one-time mowing starts with edge cutting."""
        self._one_time_options(serial_number)["edge_cut"] = bool(enabled)
        self._async_options_changed()

    async def async_set_one_time_mowing_zones(
        self, serial_number: str, zones: list[int]
    ) -> None:
        """Set local one-time mowing RTK zones."""
        self._one_time_options(serial_number)["zones"] = _normalize_zone_ids(zones)
        self._async_options_changed()

    def _async_options_changed(self) -> None:
        """Persist local one-time mowing options and refresh entities."""
        self._options_store.async_delay_save(
            lambda: self._one_time_mowing_options, OPTIONS_SAVE_DELAY
        )
        self.async_update_listeners()

    def _restore_one_time_options(self, stored: Any) -> None:
        """Restore one-time mowing options saved before a restart."""
        if not isinstance(stored, dict):
            return
        for serial_number, options in stored.items():
            if not isinstance(options, dict):
                continue
            restored = self._one_time_options(str(serial_number))
            try:
                restored["runtime"] = max(
                    10, min(120, int(options.get("runtime", restored["runtime"])))
                )
                restored["edge_cut"] = bool(options.get("edge_cut", restored["edge_cut"]))
                restored["zones"] = _normalize_zone_ids(options.get("zones") or [])
            except (TypeError, ValueError):
                _LOGGER.debug("Ignoring invalid stored one-time mowing options")

    def maintenance_threshold(self, serial_number: str, key: str) -> int:
        """Return a local maintenance interval for one mower."""
        return int(
            self._maintenance.get(serial_number, {}).get(
                key, MAINTENANCE_DEFAULTS[key]
            )
        )

    def maintenance_summary(self, serial_number: str) -> dict[str, Any]:
        """Return maintenance counters and due flags using local intervals."""
        return maintenance_summary(
            (self.data or {}).get(serial_number),
            blade_hours=self.maintenance_threshold(
                serial_number, MAINTENANCE_BLADE_HOURS
            ),
            battery_cycles_limit=self.maintenance_threshold(
                serial_number, MAINTENANCE_BATTERY_CYCLES
            ),
            blade_days=self.maintenance_threshold(serial_number, MAINTENANCE_BLADE_DAYS),
            now=datetime.now(UTC),
        )

    async def async_set_maintenance_threshold(
        self, serial_number: str, key: str, value: float
    ) -> None:
        """Store a local maintenance interval for one mower."""
        low, high = MAINTENANCE_LIMITS[key]
        self._maintenance.setdefault(serial_number, {})[key] = max(
            low, min(high, round(value))
        )
        self._maintenance_store.async_delay_save(
            lambda: self._maintenance, OPTIONS_SAVE_DELAY
        )
        self.async_update_listeners()

    def _restore_maintenance(self, stored: Any) -> None:
        """Restore maintenance intervals saved before a restart."""
        if not isinstance(stored, dict):
            return
        for serial_number, options in stored.items():
            if not isinstance(options, dict):
                continue
            restored: dict[str, int] = {}
            for key, value in options.items():
                if key not in MAINTENANCE_DEFAULTS:
                    continue
                try:
                    low, high = MAINTENANCE_LIMITS[key]
                    restored[key] = max(low, min(high, int(value)))
                except (TypeError, ValueError):
                    _LOGGER.debug("Ignoring invalid stored maintenance interval")
            if restored:
                self._maintenance[str(serial_number)] = restored

    def _check_private_cloud_api(self) -> None:
        """Warn once when pyworxcloud no longer offers the private API used here."""
        missing = [
            name for name in PRIVATE_CLOUD_ATTRIBUTES if not hasattr(self.cloud, name)
        ]
        if not missing:
            return
        _LOGGER.warning(
            "The installed pyworxcloud does not provide %s; statistics, RTK map "
            "and firmware details will not be available. This integration is "
            "tested with the pyworxcloud version pinned in manifest.json",
            ", ".join(missing),
        )

    async def async_start_configured_one_time_mowing(self, serial_number: str) -> None:
        """Start one-time mowing using local UI options."""
        await self.async_start_one_time_mowing(
            serial_number,
            self.one_time_mowing_runtime(serial_number),
            self.one_time_mowing_edge_cut(serial_number),
            self.one_time_mowing_zones(serial_number),
        )

    async def async_set_rain_delay(self, serial_number: str, minutes: int) -> None:
        """Set rain delay in minutes."""
        raindelay = getattr(self.cloud, "raindelay", None)
        if raindelay is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support rain delay updates"
            )

        await self._async_run_cloud_call(
            lambda: raindelay(serial_number, str(int(minutes)))
        )
        self._update_cached_rain_delay(serial_number, int(minutes))
        await self._async_request_device_update_best_effort(serial_number)

    async def async_set_time_extension(
        self, serial_number: str, time_extension: int
    ) -> None:
        """Set schedule time extension in percent."""
        set_time_extension = getattr(self.cloud, "set_time_extension", None)
        if set_time_extension is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support time extension updates"
            )

        await self._async_run_cloud_call(
            lambda: set_time_extension(serial_number, int(time_extension))
        )
        await self._async_request_device_update_best_effort(serial_number)

    async def async_set_lawn_size(self, serial_number: str, size_m2: int) -> None:
        """Set top-level lawn size in square meters."""
        set_lawn_size = getattr(self.cloud, "set_lawn_size", None)
        if set_lawn_size is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support lawn size updates"
            )

        await self._async_run_cloud_call(
            lambda: set_lawn_size(serial_number, int(size_m2))
        )
        self._update_cached_product_item(serial_number, lawn_size=int(size_m2))
        await self._async_request_device_update_best_effort(serial_number)

    async def async_set_lawn_perimeter(
        self, serial_number: str, perimeter_m: int
    ) -> None:
        """Set top-level lawn perimeter in meters."""
        set_lawn_perimeter = getattr(self.cloud, "set_lawn_perimeter", None)
        if set_lawn_perimeter is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support lawn perimeter updates"
            )

        await self._async_run_cloud_call(
            lambda: set_lawn_perimeter(serial_number, int(perimeter_m))
        )
        self._update_cached_product_item(
            serial_number, lawn_perimeter=int(perimeter_m)
        )
        await self._async_request_device_update_best_effort(serial_number)

    async def async_set_firmware_auto_upgrade(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Toggle vendor firmware auto-upgrades."""
        set_firmware_auto_upgrade = getattr(
            self.cloud, "set_firmware_auto_upgrade", None
        )
        if set_firmware_auto_upgrade is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support firmware auto-upgrade"
            )

        await self._async_run_cloud_call(
            lambda: set_firmware_auto_upgrade(serial_number, enabled)
        )
        self._update_cached_product_item(serial_number, firmware_auto_upgrade=enabled)
        await self._async_request_device_update_best_effort(serial_number)

    async def async_set_lock(self, serial_number: str, enabled: bool) -> None:
        """Lock or unlock the mower."""
        set_lock = getattr(self.cloud, "set_lock", None)
        if set_lock is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support lock updates"
            )

        await self._async_run_cloud_call(
            lambda: set_lock(serial_number, state=enabled)
        )
        self._update_cached_product_item(serial_number, locked=enabled)
        await self._async_request_device_update_best_effort(serial_number)

    async def async_toggle_schedule(self, serial_number: str, enabled: bool) -> None:
        """Enable or disable the mower's native schedule."""
        toggle_schedule = getattr(self.cloud, "toggle_schedule", None)
        if toggle_schedule is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support schedule toggling"
            )

        await self._async_run_cloud_call(
            lambda: toggle_schedule(serial_number, enable=enabled)
        )
        await self._async_request_device_update_best_effort(serial_number)

    async def async_start_firmware_upgrade(self, serial_number: str) -> None:
        """Queue the latest firmware update for a mower."""
        start_firmware_upgrade = getattr(self.cloud, "start_firmware_upgrade", None)
        if start_firmware_upgrade is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support firmware installs"
            )

        await self._async_run_cloud_call(
            lambda: start_firmware_upgrade(serial_number)
        )
        await self.async_get_firmware_upgrade_info(serial_number, force=True)
        await self._async_request_device_update_best_effort(serial_number)

    async def async_reset_charge_cycle_counter(self, serial_number: str) -> None:
        """Reset battery charge cycle counter after battery maintenance."""
        reset_charge_cycle_counter = getattr(
            self.cloud, "reset_charge_cycle_counter", None
        )
        if reset_charge_cycle_counter is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support battery cycle reset"
            )

        await self._async_run_cloud_call(
            lambda: reset_charge_cycle_counter(serial_number)
        )
        await self._async_request_device_update_best_effort(serial_number)

    async def async_set_cut_over_border(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Persist whether Vision border cutting may cross the lawn border."""
        set_cut_over_border = getattr(self.cloud, "set_cut_over_border", None)
        if set_cut_over_border is not None:
            await self._async_run_cloud_call(
                lambda: set_cut_over_border(serial_number, enabled)
            )
        else:
            await self._async_run_cloud_call(
                lambda: self._async_send_cut_over_border(serial_number, enabled)
            )

        self._update_cached_cut_over_border(serial_number, enabled)
        await self._async_request_device_update_best_effort(serial_number)

    async def async_set_border_distance(
        self, serial_number: str, distance_mm: int
    ) -> None:
        """Persist Vision border-cut distance in millimeters."""
        set_border_distance = getattr(self.cloud, "set_border_distance", None)
        if set_border_distance is None:
            raise HomeAssistantError(
                "The installed pyworxcloud version does not support border distance updates"
            )

        await self._async_run_cloud_call(
            lambda: set_border_distance(serial_number, int(distance_mm))
        )
        self._update_cached_border_distance(serial_number, int(distance_mm))
        await self._async_request_device_update_best_effort(serial_number)

    async def _async_send_cut_over_border(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Send the observed Vision Cloud border-cut payload for pyworxcloud 6.3.x."""
        mower = self.cloud.get_mower(serial_number)
        if mower.get("protocol") != 1:
            raise ValueError(
                "Intelligent edge cutting is only supported for protocol 1 devices"
            )

        payload = {
            "mz": {
                "s": [
                    {
                        "id": 1,
                        "c": 1,
                        "cfg": {"cut": {"ob": 1 if enabled else 0}},
                    }
                ],
                "p": [],
            }
        }
        await self.cloud.send(serial_number, json.dumps(payload))

    def _update_cached_cut_over_border(
        self, serial_number: str, enabled: bool
    ) -> None:
        """Update local cached raw config so the switch state changes immediately."""
        device = (self.data or {}).get(serial_number)
        if device is None:
            return

        raw_cfg = getattr(device, "raw_cfg", None)
        if isinstance(raw_cfg, dict):
            cut = raw_cfg.setdefault("cut", {})
            if isinstance(cut, dict):
                cut["ob"] = 1 if enabled else 0
            for zone_cut in self._raw_zone_cutting_dicts(raw_cfg):
                zone_cut["ob"] = 1 if enabled else 0

    def _update_cached_border_distance(
        self, serial_number: str, distance_mm: int
    ) -> None:
        """Update cached border distance so the number changes immediately."""
        device = (self.data or {}).get(serial_number)
        if device is None:
            return

        raw_cfg = getattr(device, "raw_cfg", None)
        if isinstance(raw_cfg, dict):
            cut = raw_cfg.setdefault("cut", {})
            if isinstance(cut, dict):
                cut["bd"] = distance_mm
                cut["co"] = distance_mm
            for zone_cut in self._raw_zone_cutting_dicts(raw_cfg):
                zone_cut["bd"] = distance_mm
                zone_cut["co"] = distance_mm

    @staticmethod
    def _raw_zone_cutting_dicts(raw_cfg: dict[str, Any]) -> list[dict[str, Any]]:
        """Return mutable zone cutting dictionaries from known protocol 1 paths."""
        result: list[dict[str, Any]] = []
        for zone_container in (("rtk", "zs"), ("mz", "s")):
            current: Any = raw_cfg
            for key in zone_container:
                if not isinstance(current, dict):
                    current = None
                    break
                current = current.get(key)
            if not isinstance(current, list | tuple):
                continue
            for zone in current:
                if not isinstance(zone, dict):
                    continue
                cfg = zone.setdefault("cfg", {})
                if not isinstance(cfg, dict):
                    continue
                cut = cfg.setdefault("cut", {})
                if isinstance(cut, dict):
                    result.append(cut)
        return result

    async def async_get_rtk_map(
        self, map_id: str | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch RTK map geometry from the private Worx maps endpoint."""
        if not map_id:
            return None

        now = datetime.now(UTC)
        cached = self._rtk_map_cache.get(map_id)
        if (
            cached is not None
            and not force
            and now - cached[0] < RTK_MAP_CACHE_TTL
        ):
            return cached[1]

        map_data = await self._api_get(f"/api/v2/maps/{map_id}")
        if isinstance(map_data, dict):
            self._rtk_map_cache[map_id] = (now, map_data)
            return map_data

        return cached[1] if cached is not None else None

    async def async_get_product_item(
        self, serial_number: str | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch product item details from the private Worx endpoint."""
        if not serial_number:
            return None

        now = datetime.now(UTC)
        cached = self._product_item_cache.get(serial_number)
        if (
            cached is not None
            and not force
            and now - cached[0] < PRODUCT_ITEM_CACHE_TTL
        ):
            return cached[1]

        product_item = await self._api_get(f"/api/v2/product-items/{serial_number}")
        if isinstance(product_item, dict):
            self._product_item_cache[serial_number] = (now, product_item)
            return product_item

        return cached[1] if cached is not None else None

    async def async_get_firmware_upgrade_info(
        self, serial_number: str | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Fetch firmware upgrade metadata from pyworxcloud/private API."""
        if not serial_number:
            return None

        now = datetime.now(UTC)
        cached = self._firmware_upgrade_cache.get(serial_number)
        if (
            cached is not None
            and not force
            and now - cached[0] < FIRMWARE_UPGRADE_CACHE_TTL
        ):
            return cached[1]

        firmware_info: dict[str, Any] | None = None
        get_firmware_upgrade_info = getattr(
            self.cloud, "get_firmware_upgrade_info", None
        )
        if get_firmware_upgrade_info is not None:
            try:
                value = await get_firmware_upgrade_info(serial_number)
                if isinstance(value, dict):
                    firmware_info = value
            except Exception:  # noqa: BLE001
                _LOGGER.debug(
                    "Could not fetch firmware upgrade info for %s",
                    serial_number,
                    exc_info=True,
                )

        if firmware_info is None:
            product_item = await self.async_get_product_item(serial_number)
            firmware_info = self._fallback_firmware_upgrade_info(product_item)

        if firmware_info is not None:
            self._firmware_upgrade_cache[serial_number] = (now, firmware_info)
            device = (self.data or {}).get(serial_number)
            if device is not None:
                setattr(device, "_worx_vision_firmware_upgrade", firmware_info)
            return firmware_info

        return cached[1] if cached is not None else None

    def product_item_data(self, serial_number: str) -> dict[str, Any] | None:
        """Return cached product item details."""
        cached = self._product_item_cache.get(serial_number)
        return None if cached is None else cached[1]

    def firmware_upgrade_data(self, serial_number: str) -> dict[str, Any] | None:
        """Return cached firmware upgrade details."""
        cached = self._firmware_upgrade_cache.get(serial_number)
        return None if cached is None else cached[1]

    def rtk_map_data(self, map_id: str | None) -> dict[str, Any] | None:
        """Return cached RTK map details."""
        if not map_id:
            return None
        cached = self._rtk_map_cache.get(map_id)
        return None if cached is None else cached[1]

    def rtk_position_trail(
        self, serial_number: str, max_points: int = 120
    ) -> list[tuple[float, float]]:
        """Return recent RTK positions for map rendering."""
        trail = self._rtk_position_trails.get(serial_number)
        if trail is None:
            return []
        return [(lat, lon) for _, lat, lon in list(trail)[-max_points:]]

    def rtk_position_timed_trail(
        self, serial_number: str, max_points: int = 120
    ) -> list[tuple[datetime, float, float]]:
        """Return recent RTK positions with timestamps for map rendering."""
        trail = self._rtk_position_trails.get(serial_number)
        if trail is None:
            return []
        return list(trail)[-max_points:]

    def area_mowed_today(self, serial_number: str) -> float | None:
        """Return the cloud counter increase since local midnight."""
        return self._statistics.area_mowed_today(
            serial_number,
            dt_util.now().date(),
        )

    def daily_area_details(self, serial_number: str) -> dict[str, Any]:
        """Return diagnostics for the cloud daily-area calculation."""
        return self._statistics.area_details(serial_number)

    def mowing_minutes_today(self, serial_number: str) -> float:
        """Return locally observed blade-active minutes since midnight."""
        return self._statistics.mowing_minutes_today(
            serial_number,
            datetime.now(UTC),
            dt_util.now().date(),
        )

    async def async_reverse_geocode_rtk_position(
        self, position: tuple[float, float] | None, *, force: bool = False
    ) -> dict[str, Any] | None:
        """Return a cached reverse-geocoded address for an RTK position."""
        if position is None:
            return None

        cache_key = self.rtk_address_cache_key(position)
        now = datetime.now(UTC)
        cached = self._rtk_address_cache.get(cache_key)
        if (
            cached is not None
            and not force
            and now - cached[0] < RTK_ADDRESS_CACHE_TTL
        ):
            return cached[1]

        async with self._rtk_address_lock:
            cached = self._rtk_address_cache.get(cache_key)
            if (
                cached is not None
                and not force
                and now - cached[0] < RTK_ADDRESS_CACHE_TTL
            ):
                return cached[1]

            lookup_latitude, lookup_longitude = self.rtk_address_lookup_position(position)
            await self._throttle_rtk_address_lookup()

            session = async_get_clientsession(self.hass)
            params = {
                "format": "jsonv2",
                "lat": f"{lookup_latitude:.{RTK_ADDRESS_COORD_PRECISION}f}",
                "lon": f"{lookup_longitude:.{RTK_ADDRESS_COORD_PRECISION}f}",
                "zoom": "18",
                "addressdetails": "1",
                "accept-language": self.hass.config.language or "en",
            }
            headers = {
                "User-Agent": RTK_ADDRESS_USER_AGENT,
                "Accept": "application/json",
            }

            try:
                async with session.get(
                    RTK_ADDRESS_ENDPOINT,
                    params=params,
                    headers=headers,
                    timeout=ClientTimeout(total=10),
                ) as response:
                    if response.status == 429 and cached is not None:
                        _LOGGER.debug(
                            "Nominatim rate-limited address lookup; using cache"
                        )
                        return cached[1]
                    response.raise_for_status()
                    address_data = await response.json()
            except (ClientError, TimeoutError, ValueError):
                _LOGGER.debug("Could not reverse-geocode RTK position", exc_info=True)
                return cached[1] if cached is not None else None

        if isinstance(address_data, dict):
            self._rtk_address_cache[cache_key] = (now, address_data)
            return address_data

        return cached[1] if cached is not None else None

    @staticmethod
    def rtk_address_cache_key(position: tuple[float, float]) -> str:
        """Return a privacy-friendlier cache key for RTK address lookups."""
        latitude, longitude = WorxVisionCoordinator.rtk_address_lookup_position(position)
        return (
            f"{latitude:.{RTK_ADDRESS_COORD_PRECISION}f},"
            f"{longitude:.{RTK_ADDRESS_COORD_PRECISION}f}"
        )

    @staticmethod
    def rtk_address_lookup_position(position: tuple[float, float]) -> tuple[float, float]:
        """Return rounded coordinates used for reverse-geocoding."""
        return (
            round(position[0], RTK_ADDRESS_COORD_PRECISION),
            round(position[1], RTK_ADDRESS_COORD_PRECISION),
        )

    async def _throttle_rtk_address_lookup(self) -> None:
        """Keep public reverse-geocoding requests below one request per second."""
        if self._last_rtk_address_lookup is not None:
            elapsed = datetime.now(UTC) - self._last_rtk_address_lookup
            remaining = 1 - elapsed.total_seconds()
            if remaining > 0:
                await asyncio.sleep(remaining)
        self._last_rtk_address_lookup = datetime.now(UTC)

    def _schedule_push_update(self, device: DeviceHandler) -> None:
        """Schedule a pushed device update on HA loop."""
        try:
            self.hass.loop.call_soon_threadsafe(self._create_push_update_task, device)
        except RuntimeError:
            _LOGGER.debug("Ignoring push update after HA loop shutdown")

    def _schedule_api_refresh(self) -> None:
        """Schedule API cache refresh on HA loop."""
        try:
            self.hass.loop.call_soon_threadsafe(self._create_api_refresh_task)
        except RuntimeError:
            _LOGGER.debug("Ignoring API update after HA loop shutdown")

    def _create_push_update_task(self, device: DeviceHandler) -> None:
        """Create task for a pushed update."""
        self.hass.async_create_task(self._handle_push_update(device))

    def _create_api_refresh_task(self) -> None:
        """Create task for API cache refresh."""
        self.hass.async_create_task(self.async_request_refresh())

    async def _enrich_device(self, serial_number: str, device: DeviceHandler) -> None:
        """Attach private API details to the cached device object."""
        product_item = await self.async_get_product_item(serial_number)
        if product_item is not None:
            setattr(device, "_worx_vision_product_item", product_item)
            cached_product_item = self._product_item_cache.get(serial_number)
            if cached_product_item is not None:
                setattr(
                    device,
                    "_worx_vision_product_item_updated_at",
                    cached_product_item[0],
                )

        firmware_info = await self.async_get_firmware_upgrade_info(serial_number)
        if firmware_info is not None:
            setattr(device, "_worx_vision_firmware_upgrade", firmware_info)

        map_id = self._device_rtk_map_id(device)
        map_data = await self.async_get_rtk_map(map_id)
        if map_data is not None:
            setattr(device, "_worx_vision_rtk_map", map_data)

        self._remember_rtk_position(serial_number, device)
        self._update_daily_statistics(serial_number, device)

    async def _api_get(self, path: str) -> Any:
        """Fetch a private Worx API path using pyworxcloud's session/token.

        A rejected login is remembered for the running refresh, which then
        starts re-authentication. A rate limit pauses these optional REST
        requests for an hour instead of repeating them.
        """
        if self._rest_backoff_until is not None:
            if datetime.now(UTC) < self._rest_backoff_until:
                return None
            self._rest_backoff_until = None

        api = getattr(self.cloud, "_api", None)
        if api is None:
            _LOGGER.debug("Cannot fetch Worx API path %s: API object missing", path)
            return None

        try:
            await api.check_token()
            endpoint = getattr(getattr(api, "cloud", None), "ENDPOINT", None)
            if endpoint is None:
                endpoint = getattr(getattr(self.cloud, "_cloud", None), "ENDPOINT", None)
            if endpoint is None:
                _LOGGER.debug("Cannot fetch Worx API path %s: endpoint missing", path)
                return None

            return await AGET(
                f"https://{endpoint}{path}",
                HEADERS(api.access_token),
                session=await api._ensure_session(),
            )
        except AuthorizationError as err:
            _LOGGER.debug("Worx rejected the login for API path %s", path)
            self._rest_auth_error = err
            return None
        except TooManyRequestsError:
            self._rest_backoff_until = datetime.now(UTC) + REST_RATE_LIMIT_BACKOFF
            _LOGGER.warning(
                "Worx cloud rate limit; pausing statistics, map and firmware "
                "requests for %s",
                REST_RATE_LIMIT_BACKOFF,
            )
            return None
        except Exception:  # noqa: BLE001
            _LOGGER.debug("Could not fetch Worx API path %s", path, exc_info=True)
            return None

    @staticmethod
    def _device_rtk_map_id(device: DeviceHandler) -> str | None:
        """Return RTK map id directly from a pyworxcloud device payload."""
        cfg = getattr(device, "raw_cfg", {}) or {}
        if not isinstance(cfg, dict):
            return None
        rtk = cfg.get("rtk") or {}
        if not isinstance(rtk, dict):
            return None
        value = rtk.get("map")
        return None if value is None else str(value)

    @staticmethod
    def _fallback_firmware_upgrade_info(
        product_item: dict[str, Any] | None
    ) -> dict[str, Any] | None:
        """Build basic firmware metadata when the OTA endpoint is unavailable."""
        if not isinstance(product_item, dict):
            return None

        current_version = product_item.get("firmware_version")
        capabilities = product_item.get("capabilities") or []
        ota_supported = (
            isinstance(capabilities, list | tuple) and "ota_upgrade" in capabilities
        )
        return {
            "current_version": current_version,
            "latest_version": current_version,
            "update_available": False,
            "ota_supported": ota_supported,
            "auto_upgrade": product_item.get("firmware_auto_upgrade"),
        }

    def _remember_rtk_position(
        self, serial_number: str, device: DeviceHandler
    ) -> None:
        """Keep an in-memory RTK trail for dashboards and the map camera."""
        position = rtk_position(device)
        if position is None:
            return

        latitude, longitude = position
        trail = self._rtk_position_trails.setdefault(
            serial_number, deque(maxlen=RTK_TRAIL_MAX_POINTS)
        )
        if trail:
            _, previous_latitude, previous_longitude = trail[-1]
            if (
                round(previous_latitude, 7) == round(latitude, 7)
                and round(previous_longitude, 7) == round(longitude, 7)
            ):
                return

        trail.append((datetime.now(UTC), latitude, longitude))
        setattr(device, "_worx_vision_rtk_trail", list(trail))

    def _preserve_enriched_attributes(
        self, serial_number: str, device: DeviceHandler
    ) -> None:
        """Keep cached API enrichment on MQTT-only push updates."""
        previous = (self.data or {}).get(serial_number)
        if previous is None:
            return

        for attr in (
            "_worx_vision_product_item",
            "_worx_vision_product_item_updated_at",
            "_worx_vision_firmware_upgrade",
            "_worx_vision_rtk_map",
        ):
            if hasattr(device, attr) or not hasattr(previous, attr):
                continue
            setattr(device, attr, getattr(previous, attr))

    def _update_daily_statistics(
        self, serial_number: str, device: DeviceHandler
    ) -> None:
        """Update persisted daily counters from one device snapshot."""
        product_item = getattr(device, "_worx_vision_product_item", {}) or {}
        status_id = get_dict_value(getattr(device, "status", {}) or {}, "id")
        try:
            mowing_active = int(status_id) in MOWING_STATUS_IDS
        except (TypeError, ValueError):
            mowing_active = False

        now_utc = datetime.now(UTC)
        local_now = dt_util.as_local(now_utc)
        local_midnight_utc = datetime.combine(
            local_now.date(),
            time.min,
            tzinfo=local_now.tzinfo,
        ).astimezone(UTC)
        changed = self._statistics.update(
            serial_number,
            area_total=get_dict_value(product_item, "area_mowed"),
            mowing_active=mowing_active,
            now_utc=now_utc,
            local_day=local_now.date(),
            local_midnight_utc=local_midnight_utc,
        )
        if changed:
            if self._statistics_save_pending:
                return
            self._statistics_save_pending = True
            self._statistics_store.async_delay_save(
                self._statistics_store_data,
                STATISTICS_SAVE_DELAY,
            )

    def _statistics_store_data(self) -> dict[str, Any]:
        """Return current statistics and allow scheduling the next save."""
        self._statistics_save_pending = False
        return self._statistics.as_dict()

    def _update_cached_product_item(self, serial_number: str, **fields: Any) -> None:
        """Patch cached product item fields after a successful write."""
        cached = self._product_item_cache.get(serial_number)
        if cached is not None:
            cached[1].update(fields)

        device = (self.data or {}).get(serial_number)
        if device is not None:
            product_item = getattr(device, "_worx_vision_product_item", None)
            if isinstance(product_item, dict):
                product_item.update(fields)

    def _update_cached_rain_delay(self, serial_number: str, minutes: int) -> None:
        """Patch cached rain delay after a successful write."""
        device = (self.data or {}).get(serial_number)
        if device is None:
            return

        rainsensor = getattr(device, "rainsensor", None)
        if isinstance(rainsensor, dict):
            rainsensor["delay"] = minutes
        elif rainsensor is not None and hasattr(rainsensor, "delay"):
            try:
                setattr(rainsensor, "delay", minutes)
            except Exception:  # noqa: BLE001
                _LOGGER.debug("Could not update cached rain delay", exc_info=True)
