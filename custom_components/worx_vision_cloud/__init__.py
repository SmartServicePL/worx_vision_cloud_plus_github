"""Worx Vision Cloud Plus integration.

A small custom integration focused on Worx Landroid Vision Cloud mowers.
It uses pyworxcloud for the reverse-engineered Positec/Worx cloud API and
adds curated Home Assistant entities, schedule support and RTK map rendering.
"""
from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry, ConfigEntryState
from homeassistant.const import ATTR_ENTITY_ID, CONF_EMAIL, CONF_PASSWORD
from homeassistant.core import HomeAssistant, ServiceCall
from homeassistant.exceptions import (
    ConfigEntryAuthFailed,
    ConfigEntryNotReady,
    ServiceValidationError,
)
from homeassistant.helpers import (
    area_registry as ar,
    device_registry as dr,
    entity_registry as er,
)
import homeassistant.helpers.config_validation as cv
from homeassistant.helpers.typing import ConfigType
from homeassistant.util import slugify
import voluptuous as vol

from pyworxcloud import WorxCloud
from pyworxcloud.exceptions import AuthorizationError, TooManyRequestsError

from .const import (
    ATTR_EDGE_CUT,
    ATTR_RUNTIME,
    ATTR_ZONES,
    CONF_CLOUD,
    CONF_VERIFY_SSL,
    DEFAULT_CLOUD,
    DEFAULT_VERIFY_SSL,
    DOMAIN,
    PLATFORMS,
    SERVICE_START_ONE_TIME_MOWING,
    raw_entities_enabled,
)
from .coordinator import WorxVisionCoordinator

_LOGGER = logging.getLogger(__name__)

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

START_ONE_TIME_MOWING_SCHEMA = vol.Schema(
    {
        vol.Required(ATTR_ENTITY_ID): cv.entity_id,
        vol.Optional(ATTR_RUNTIME, default=60): vol.All(
            vol.Coerce(int), vol.Range(min=0, max=120)
        ),
        vol.Optional(ATTR_EDGE_CUT, default=False): cv.boolean,
        vol.Optional(ATTR_ZONES, default=[]): lambda value: _service_zone_ids(value),
    }
)


@dataclass
class WorxVisionRuntimeData:
    """Runtime objects kept for one config entry."""

    cloud: WorxCloud
    coordinator: WorxVisionCoordinator
    expose_raw: bool = False


type WorxConfigEntry = ConfigEntry[WorxVisionRuntimeData]


async def async_setup(hass: HomeAssistant, config: ConfigType) -> bool:
    """Register integration services.

    Services are registered here, not in async_setup_entry, so automations
    and blueprints always find them, also while the Worx cloud is unreachable
    and the config entry is still retrying its setup.
    """
    del config
    _async_setup_services(hass)
    return True


async def async_setup_entry(hass: HomeAssistant, entry: WorxConfigEntry) -> bool:
    """Set up Worx Vision Cloud Plus from a config entry."""
    username = entry.data[CONF_EMAIL]
    password = entry.data[CONF_PASSWORD]
    cloud_name = entry.data.get(CONF_CLOUD, DEFAULT_CLOUD)
    verify_ssl = entry.data.get(CONF_VERIFY_SSL, DEFAULT_VERIFY_SSL)

    cloud = WorxCloud(
        username=username,
        password=password,
        cloud=cloud_name,
        verify_ssl=verify_ssl,
        tz=hass.config.time_zone,
        command_timeout=30.0,
        deduplicate_inflight_commands=True,
    )

    try:
        await cloud.authenticate()
        await cloud.connect()
    except AuthorizationError as err:
        await _safe_disconnect(cloud)
        raise ConfigEntryAuthFailed("Invalid Worx/Landroid credentials") from err
    except TooManyRequestsError as err:
        await _safe_disconnect(cloud)
        raise ConfigEntryNotReady("Worx Cloud rate limit; try again later") from err
    except Exception as err:  # noqa: BLE001 - HA should retry setup
        await _safe_disconnect(cloud)
        raise ConfigEntryNotReady(f"Could not connect to Worx Cloud: {err}") from err

    coordinator = WorxVisionCoordinator(hass, cloud, entry)
    try:
        await coordinator.async_setup()
        await coordinator.async_config_entry_first_refresh()
    except Exception:
        await coordinator.async_shutdown()
        await _safe_disconnect(cloud)
        raise

    if not coordinator.data:
        await coordinator.async_shutdown()
        await _safe_disconnect(cloud)
        raise ConfigEntryNotReady("No cloud mower found on this Worx/Landroid account")

    _async_migrate_entity_registry(hass, coordinator.data, entry)

    expose_raw = raw_entities_enabled(entry)
    if not expose_raw:
        _async_remove_raw_entities(hass, coordinator.data, entry)

    entry.runtime_data = WorxVisionRuntimeData(
        cloud=cloud,
        coordinator=coordinator,
        expose_raw=expose_raw,
    )

    entry.async_on_unload(entry.add_update_listener(_async_entry_updated))
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    return True


async def _async_entry_updated(hass: HomeAssistant, entry: WorxConfigEntry) -> None:
    """Reload the entry when the raw-entity option changed.

    Other entry updates (for example a new password from re-authentication)
    reload the entry on their own and must not trigger a second reload.
    """
    runtime: WorxVisionRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is None or runtime.expose_raw == raw_entities_enabled(entry):
        return
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: WorxConfigEntry) -> bool:
    """Unload a config entry."""
    unload_ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if not unload_ok:
        return False

    runtime: WorxVisionRuntimeData | None = getattr(entry, "runtime_data", None)
    if runtime is not None:
        await runtime.coordinator.async_shutdown()
        await _safe_disconnect(runtime.cloud)

    return unload_ok


def _loaded_runtime_data(
    hass: HomeAssistant, preferred_entry_id: str | None, serial_number: str
) -> WorxVisionRuntimeData | None:
    """Return runtime data of the loaded entry that manages the given mower."""
    entries = sorted(
        hass.config_entries.async_entries(DOMAIN),
        key=lambda item: item.entry_id != preferred_entry_id,
    )
    for config_entry in entries:
        if config_entry.state is not ConfigEntryState.LOADED:
            continue
        runtime = getattr(config_entry, "runtime_data", None)
        if runtime is not None and serial_number in (runtime.coordinator.data or {}):
            return runtime
    return None


def _service_zone_ids(value: Any) -> list[int]:
    """Normalize service-provided zone IDs."""
    if value in (None, "", []):
        return []
    if isinstance(value, str):
        values = [item.strip() for item in value.replace(";", ",").split(",")]
    elif isinstance(value, list | tuple | set):
        values = list(value)
    else:
        values = [value]

    zone_ids: list[int] = []
    for item in values:
        if item in (None, ""):
            continue
        try:
            zone_id = int(item)
        except (TypeError, ValueError) as err:
            raise vol.Invalid("Zone IDs must be positive numbers") from err
        if zone_id < 1:
            raise vol.Invalid("Zone IDs must be positive numbers")
        if zone_id not in zone_ids:
            zone_ids.append(zone_id)
    return zone_ids


def _async_setup_services(hass: HomeAssistant) -> None:
    """Register integration-level services."""
    if hass.services.has_service(DOMAIN, SERVICE_START_ONE_TIME_MOWING):
        return

    async def async_start_one_time_mowing(call: ServiceCall) -> None:
        entity_id = call.data[ATTR_ENTITY_ID]
        entity_entry = er.async_get(hass).async_get(entity_id)
        if (
            entity_entry is None
            or entity_entry.platform != DOMAIN
            or not entity_entry.unique_id.endswith("_mower")
        ):
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="invalid_mower_entity",
                translation_placeholders={"entity_id": entity_id},
            )

        serial_number = entity_entry.unique_id.removesuffix("_mower")
        runtime_data = _loaded_runtime_data(
            hass, entity_entry.config_entry_id, serial_number
        )
        if runtime_data is None:
            config_entry = (
                hass.config_entries.async_get_entry(entity_entry.config_entry_id)
                if entity_entry.config_entry_id
                else None
            )
            raise ServiceValidationError(
                translation_domain=DOMAIN,
                translation_key="integration_not_loaded",
                translation_placeholders={
                    "entry": config_entry.title if config_entry else entity_id,
                    "state": (
                        config_entry.state.value
                        if config_entry is not None
                        else ConfigEntryState.NOT_LOADED.value
                    ),
                },
            )

        await runtime_data.coordinator.async_start_one_time_mowing(
            serial_number,
            call.data[ATTR_RUNTIME],
            call.data[ATTR_EDGE_CUT],
            call.data[ATTR_ZONES],
        )

    hass.services.async_register(
        DOMAIN,
        SERVICE_START_ONE_TIME_MOWING,
        async_start_one_time_mowing,
        schema=START_ONE_TIME_MOWING_SCHEMA,
    )


def _async_remove_raw_entities(
    hass: HomeAssistant,
    devices: dict,
    entry: ConfigEntry,
) -> None:
    """Remove raw payload entities after the raw-entity option was turned off."""
    registry = er.async_get(hass)
    prefixes = tuple(f"{serial_number}_raw_" for serial_number in devices)
    if not prefixes:
        return
    for entity_entry in er.async_entries_for_config_entry(registry, entry.entry_id):
        if entity_entry.unique_id.startswith(prefixes):
            registry.async_remove(entity_entry.entity_id)


async def _safe_disconnect(cloud: WorxCloud) -> None:
    """Disconnect cloud object without failing HA unload/setup."""
    try:
        await cloud.disconnect()
    except Exception:  # noqa: BLE001
        _LOGGER.debug("Ignoring error while disconnecting Worx cloud", exc_info=True)


def _async_migrate_entity_registry(
    hass: HomeAssistant,
    devices: dict,
    entry: ConfigEntry,
) -> None:
    """Clean up entity registry changes introduced by newer entity names."""
    registry = er.async_get(hass)
    device_registry = dr.async_get(hass)
    area_registry = ar.async_get(hass)
    account_email = str(entry.data.get(CONF_EMAIL, "") or "").strip()
    account_prefix = f"{slugify(account_email)}_" if account_email else ""

    for serial_number in devices:
        for domain, unique_id in (
            ("sensor", f"{serial_number}_distance_driven_total"),
            ("sensor", f"{serial_number}_distance_covered"),
            ("sensor", f"{serial_number}_lawn_perimeter"),
            ("binary_sensor", f"{serial_number}_battery_charging"),
            ("binary_sensor", f"{serial_number}_radio_link_pending"),
            ("binary_sensor", f"{serial_number}_schedule_border_cut"),
            ("switch", f"{serial_number}_auto_schedule"),
            ("switch", f"{serial_number}_schedule_border_cut"),
        ):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if entity_id is not None:
                registry.async_remove(entity_id)

        if account_prefix:
            for unique_suffix in (
                "area_mowed_total",
                "cloud_statistics_updated",
                "next_schedule",
                "estimated_area_mowed_today",
                "estimated_daily_progress",
            ):
                entity_id = registry.async_get_entity_id(
                    "sensor",
                    DOMAIN,
                    f"{serial_number}_{unique_suffix}",
                )
                if entity_id is None:
                    continue
                object_id = entity_id.partition(".")[2]
                if not object_id.startswith(account_prefix):
                    continue
                new_entity_id = f"sensor.{object_id.removeprefix(account_prefix)}"
                if registry.async_get(new_entity_id) is None:
                    registry.async_update_entity(
                        entity_id,
                        new_entity_id=new_entity_id,
                    )

        get_device_by_identifier = getattr(
            device_registry, "async_get_device_by_identifier", None
        )
        if get_device_by_identifier is not None:
            device_entry = get_device_by_identifier(
                (DOMAIN, str(serial_number)), entry.entry_id
            )
        else:
            device_entry = device_registry.async_get_device(
                identifiers={(DOMAIN, str(serial_number))}
            )
        if device_entry is not None and device_entry.area_id is not None:
            area_entry = area_registry.async_get_area(device_entry.area_id)
            if (
                area_entry is not None
                and account_email
                and area_entry.name.casefold() == account_email.casefold()
            ):
                device_registry.async_update_device(
                    device_entry.id,
                    area_id=None,
                )

        rain_entity_id = registry.async_get_entity_id(
            "binary_sensor", DOMAIN, f"{serial_number}_rain_triggered"
        )
        if (
            rain_entity_id is not None
            and rain_entity_id.endswith("_czujnik_deszczu_aktywny")
        ):
            new_entity_id = rain_entity_id.removesuffix(
                "czujnik_deszczu_aktywny"
            ) + "czujnik_opadow_deszczu"
            if registry.async_get(new_entity_id) is None:
                registry.async_update_entity(
                    rain_entity_id, new_entity_id=new_entity_id
                )
