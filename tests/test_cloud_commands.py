"""Tests for cloud command errors, reconnects, refresh failures and services."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any
import unittest

import ha_stubs

COORDINATOR = ha_stubs.load("coordinator")
INTEGRATION = ha_stubs.load("__init__")
LAWN_MOWER = ha_stubs.load("lawn_mower")
CONST = ha_stubs.load("const")

import pyworxcloud.exceptions as worx_exceptions  # noqa: E402
from homeassistant.exceptions import (  # noqa: E402
    ConfigEntryAuthFailed,
    HomeAssistantError,
    ServiceValidationError,
)
from homeassistant.helpers.update_coordinator import UpdateFailed  # noqa: E402

SERIAL = "202412345678"


class FakeMqtt:
    """Records published MQTT commands."""

    def __init__(self) -> None:
        self.published: list[tuple[Any, ...]] = []
        self.errors: list[Exception] = []

    async def apublish(self, serial, topic, message, protocol) -> None:
        if self.errors:
            raise self.errors.pop(0)
        self.published.append((serial, topic, message, protocol))


class FakeCloud:
    """pyworxcloud WorxCloud stand-in."""

    def __init__(self) -> None:
        self.mqtt_connected = True
        self.mqtt = FakeMqtt()
        self.connect_calls = 0
        self.connect_error: Exception | None = None
        self.update_error: Exception | None = None
        self.calls: list[str] = []
        self.mower: dict[str, Any] = {
            "online": True,
            "protocol": 1,
            "uuid": "uuid-1",
            "mqtt_topics": {"command_in": "topic/in"},
        }
        self.devices: dict[str, Any] = {}
        self._api = None

    async def connect(self) -> None:
        self.connect_calls += 1
        await asyncio.sleep(0)
        if self.connect_error is not None:
            raise self.connect_error
        self.mqtt_connected = True

    async def update(self, serial_number: str) -> None:
        self.calls.append("update")
        if self.update_error is not None:
            raise self.update_error

    def get_mower(self, serial_number: str) -> dict[str, Any]:
        if isinstance(self.mower, Exception):
            raise self.mower
        return self.mower

    def set_callback(self, event, callback) -> None:
        return None


class FakeEntry:
    """Config entry stand-in."""

    def __init__(self, entry_id: str = "entry-1") -> None:
        self.entry_id = entry_id
        self.title = "Worx Vision Cloud (user@example.com)"
        self.state = ha_stubs.ConfigEntryState.SETUP_RETRY
        self.data: dict[str, Any] = {}
        self.options: dict[str, Any] = {}
        self.reauth_started = 0

    def async_start_reauth(self, hass) -> None:
        self.reauth_started += 1


def _coordinator(cloud: FakeCloud | None = None, entry: FakeEntry | None = None):
    coordinator = COORDINATOR.WorxVisionCoordinator(
        SimpleNamespace(), cloud or FakeCloud(), entry or FakeEntry()
    )
    coordinator.data = {SERIAL: SimpleNamespace(serial_number=SERIAL)}
    return coordinator


class CloudCommandTests(unittest.IsolatedAsyncioTestCase):
    """Lawn mower commands and the shared cloud-call wrapper (I1, I5)."""

    async def test_library_errors_become_home_assistant_errors(self) -> None:
        cases = (
            (worx_exceptions.TimeoutException("late"), "cloud_command_timeout"),
            (worx_exceptions.TooManyRequestsError("429"), "cloud_rate_limited"),
            (RuntimeError("boom"), "cloud_command_failed"),
        )
        for error, key in cases:
            coordinator = _coordinator()

            async def fail(error=error) -> None:
                raise error

            with self.subTest(key=key):
                with self.assertRaises(HomeAssistantError) as ctx:
                    await coordinator.async_cloud_command(SERIAL, fail, ensure_mqtt=True)
                self.assertEqual(ctx.exception.translation_key, key)

        self.assertEqual(
            ctx.exception.translation_placeholders, {"error": "boom"}
        )

    async def test_rejected_login_starts_reauth(self) -> None:
        entry = FakeEntry()
        coordinator = _coordinator(entry=entry)

        async def fail() -> None:
            raise worx_exceptions.AuthorizationError("401")

        with self.assertRaises(HomeAssistantError) as ctx:
            await coordinator.async_cloud_command(SERIAL, fail)
        self.assertEqual(ctx.exception.translation_key, "cloud_auth_failed")
        self.assertEqual(entry.reauth_started, 1)

    async def test_command_is_not_failed_by_refresh_error(self) -> None:
        cloud = FakeCloud()
        cloud.update_error = worx_exceptions.TimeoutException("no state in 30 s")
        coordinator = _coordinator(cloud)
        sent: list[str] = []

        async def home() -> None:
            sent.append("home")

        await coordinator.async_cloud_command(SERIAL, home, ensure_mqtt=True)
        self.assertEqual(sent, ["home"])
        self.assertEqual(cloud.calls, ["update"])

    async def test_dropped_connection_reconnects_and_retries_once(self) -> None:
        cloud = FakeCloud()
        coordinator = _coordinator(cloud)
        attempts: list[int] = []

        async def start() -> None:
            attempts.append(1)
            if len(attempts) == 1:
                cloud.mqtt_connected = False
                raise worx_exceptions.NoConnectionError("stale")

        await coordinator.async_cloud_command(SERIAL, start, ensure_mqtt=True)
        self.assertEqual(len(attempts), 2)
        self.assertEqual(cloud.connect_calls, 1)

    async def test_concurrent_commands_reconnect_once(self) -> None:
        cloud = FakeCloud()
        cloud.mqtt_connected = False
        coordinator = _coordinator(cloud)

        async def noop() -> None:
            return None

        await asyncio.gather(
            coordinator.async_cloud_command(SERIAL, noop, ensure_mqtt=True),
            coordinator.async_cloud_command(SERIAL, noop, ensure_mqtt=True),
        )
        self.assertEqual(cloud.connect_calls, 1)

    async def test_lawn_mower_dock_raises_home_assistant_error(self) -> None:
        cloud = FakeCloud()

        async def home(serial_number: str) -> None:
            raise worx_exceptions.NoConnectionError("offline broker")

        cloud.home = home
        cloud.connect_error = RuntimeError("broker unreachable")
        coordinator = _coordinator(cloud)
        mower = LAWN_MOWER.WorxVisionLawnMower(coordinator, FakeEntry(), SERIAL)

        with self.assertRaises(HomeAssistantError):
            await mower.async_dock()

    def test_lawn_mower_always_publishes_rtk_at_station(self) -> None:
        coordinator = _coordinator()
        mower = LAWN_MOWER.WorxVisionLawnMower(coordinator, FakeEntry(), SERIAL)
        attrs = mower.extra_state_attributes
        self.assertIn("rtk_at_station", attrs)
        self.assertIsNone(attrs["rtk_at_station"])
        for key in ("rain_delay", "error_description", "status_id"):
            self.assertIn(key, attrs)


class OneTimeMowingTests(unittest.IsolatedAsyncioTestCase):
    """start_one_time_mowing service path (I2, issue #5)."""

    async def test_cloud_errors_are_wrapped(self) -> None:
        cloud = FakeCloud()
        cloud.mower = RuntimeError("REST timeout")
        coordinator = _coordinator(cloud)
        with self.assertRaises(HomeAssistantError) as ctx:
            await coordinator.async_start_one_time_mowing(SERIAL, 30)
        self.assertEqual(ctx.exception.translation_key, "cloud_command_failed")

        cloud = FakeCloud()
        cloud.mqtt.errors.append(worx_exceptions.TooManyRequestsError("429"))
        coordinator = _coordinator(cloud)
        with self.assertRaises(HomeAssistantError) as ctx:
            await coordinator.async_start_one_time_mowing(SERIAL, 30)
        self.assertEqual(ctx.exception.translation_key, "cloud_rate_limited")

    async def test_offline_mower_is_reported(self) -> None:
        cloud = FakeCloud()
        cloud.mower["online"] = False
        coordinator = _coordinator(cloud)
        with self.assertRaises(HomeAssistantError) as ctx:
            await coordinator.async_start_one_time_mowing(SERIAL, 30)
        self.assertEqual(ctx.exception.translation_key, "mower_offline")
        self.assertEqual(cloud.mqtt.published, [])

    async def test_sent_command_is_not_failed_by_refresh_error(self) -> None:
        cloud = FakeCloud()
        cloud.update_error = worx_exceptions.TimeoutException("no state")
        coordinator = _coordinator(cloud)
        await coordinator.async_start_one_time_mowing(SERIAL, 45, True, [])
        self.assertEqual(len(cloud.mqtt.published), 1)
        message = cloud.mqtt.published[0][2]
        self.assertEqual(message["sc"]["once"]["time"], 45)

    async def test_publish_retry_reconnects_once(self) -> None:
        cloud = FakeCloud()
        cloud.mqtt.errors.append(worx_exceptions.NoConnectionError("stale"))
        coordinator = _coordinator(cloud)
        await coordinator.async_start_one_time_mowing(SERIAL, 30)
        self.assertEqual(cloud.connect_calls, 1)
        self.assertEqual(len(cloud.mqtt.published), 1)

    async def test_vision_zones_are_sent_with_a_warning(self) -> None:
        cloud = FakeCloud()
        coordinator = _coordinator(cloud)
        with self.assertLogs(COORDINATOR.__name__, level="WARNING") as logs:
            await coordinator.async_start_one_time_mowing(SERIAL, 60, True, [3])
        self.assertTrue(any("may ignore the selected zones" in line for line in logs.output))
        message = cloud.mqtt.published[0][2]
        self.assertEqual(message["sc"]["once"]["cfg"]["cut"]["z"], [3])


class RefreshTests(unittest.IsolatedAsyncioTestCase):
    """Periodic refresh: availability and re-authentication (I4, I6)."""

    async def test_lost_connection_makes_refresh_fail(self) -> None:
        cloud = FakeCloud()
        cloud.mqtt_connected = False
        cloud.connect_error = RuntimeError("broker unreachable")
        coordinator = _coordinator(cloud)
        with self.assertRaises(UpdateFailed):
            await coordinator._async_update_data()

    async def test_rejected_login_while_reconnecting_starts_reauth(self) -> None:
        cloud = FakeCloud()
        cloud.mqtt_connected = False
        cloud.connect_error = worx_exceptions.AuthorizationError("401")
        coordinator = _coordinator(cloud)
        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_reconnect_success_returns_data(self) -> None:
        cloud = FakeCloud()
        cloud.mqtt_connected = False
        coordinator = _coordinator(cloud)
        self.assertEqual(await coordinator._async_update_data(), {})
        self.assertEqual(cloud.connect_calls, 1)

    async def test_rest_auth_error_raises_config_entry_auth_failed(self) -> None:
        cloud = FakeCloud()
        cloud.devices = {"x": SimpleNamespace(serial_number=SERIAL, raw_cfg={})}

        class Api:
            async def check_token(self) -> None:
                raise worx_exceptions.AuthorizationError("expired")

        cloud._api = Api()
        coordinator = _coordinator(cloud)
        with self.assertRaises(ConfigEntryAuthFailed):
            await coordinator._async_update_data()

    async def test_rate_limit_pauses_rest_requests(self) -> None:
        cloud = FakeCloud()
        calls: list[int] = []

        class Api:
            async def check_token(self) -> None:
                calls.append(1)
                raise worx_exceptions.TooManyRequestsError("429")

        cloud._api = Api()
        coordinator = _coordinator(cloud)
        self.assertIsNone(await coordinator._api_get("/api/v2/product-items/x"))
        self.assertIsNone(await coordinator._api_get("/api/v2/product-items/x"))
        self.assertEqual(len(calls), 1)

    def test_push_update_restores_availability(self) -> None:
        coordinator = _coordinator()
        coordinator.last_update_success = False
        coordinator._async_publish_data({SERIAL: SimpleNamespace()})
        self.assertTrue(coordinator.last_update_success)


class MaintenanceStorageTests(unittest.IsolatedAsyncioTestCase):
    """Local maintenance intervals (issue #6)."""

    async def test_defaults_keep_previous_thresholds(self) -> None:
        coordinator = _coordinator()
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "blade_hours"), 100)
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "battery_cycles"), 500)
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "blade_days"), 0)

    async def test_values_are_clamped_and_stored(self) -> None:
        coordinator = _coordinator()
        await coordinator.async_set_maintenance_threshold(SERIAL, "blade_hours", 100.4)
        await coordinator.async_set_maintenance_threshold(SERIAL, "battery_cycles", 99999)
        await coordinator.async_set_maintenance_threshold(SERIAL, "blade_days", -5)
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "blade_hours"), 100)
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "battery_cycles"), 3000)
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "blade_days"), 0)
        self.assertEqual(
            coordinator._maintenance_store.saved[SERIAL]["blade_hours"], 100
        )

    async def test_restore_ignores_invalid_values(self) -> None:
        coordinator = _coordinator()
        coordinator._restore_maintenance(
            {SERIAL: {"blade_hours": "200", "battery_cycles": "x", "other": 1}}
        )
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "blade_hours"), 200)
        self.assertEqual(coordinator.maintenance_threshold(SERIAL, "battery_cycles"), 500)

    async def test_summary_uses_local_intervals(self) -> None:
        coordinator = _coordinator()
        coordinator.data[SERIAL]._worx_vision_product_item = {
            "blade_work_time": 6100,
            "blade_work_time_reset": 0,
            "battery_charge_cycles": 520,
        }
        summary = coordinator.maintenance_summary(SERIAL)
        self.assertTrue(summary["blade_service_due"])
        self.assertTrue(summary["battery_service_due"])
        await coordinator.async_set_maintenance_threshold(SERIAL, "blade_hours", 200)
        summary = coordinator.maintenance_summary(SERIAL)
        self.assertFalse(summary["blade_service_due"])


class FakeServices:
    """hass.services stand-in."""

    def __init__(self) -> None:
        self.handlers: dict[tuple[str, str], Any] = {}

    def has_service(self, domain: str, service: str) -> bool:
        return (domain, service) in self.handlers

    def async_register(self, domain, service, handler, schema=None) -> None:
        self.handlers[(domain, service)] = handler

    def async_remove(self, domain, service) -> None:
        self.handlers.pop((domain, service), None)


class FakeRegistry:
    """Entity registry stand-in."""

    def __init__(self, entries: dict[str, Any]) -> None:
        self.entries = entries

    def async_get(self, entity_id: str):
        return self.entries.get(entity_id)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    """Service registration in async_setup (I3)."""

    def _hass(self, entry: FakeEntry) -> SimpleNamespace:
        registry = FakeRegistry(
            {
                "lawn_mower.worx": SimpleNamespace(
                    entity_id="lawn_mower.worx",
                    platform=CONST.DOMAIN,
                    unique_id=f"{SERIAL}_mower",
                    config_entry_id=entry.entry_id,
                ),
                "switch.other": SimpleNamespace(
                    entity_id="switch.other",
                    platform="other",
                    unique_id="x",
                    config_entry_id=None,
                ),
            }
        )
        return SimpleNamespace(
            services=FakeServices(),
            data={},
            entity_registry=registry,
            config_entries=SimpleNamespace(
                async_get_entry=lambda entry_id: entry
                if entry_id == entry.entry_id
                else None,
                async_entries=lambda domain: [entry] if domain == CONST.DOMAIN else [],
            ),
        )

    async def test_service_exists_before_entry_is_loaded(self) -> None:
        entry = FakeEntry()
        hass = self._hass(entry)
        self.assertTrue(await INTEGRATION.async_setup(hass, {}))
        key = (CONST.DOMAIN, CONST.SERVICE_START_ONE_TIME_MOWING)
        self.assertIn(key, hass.services.handlers)
        self.assertEqual(INTEGRATION.CONFIG_SCHEMA, ("config_entry_only", CONST.DOMAIN))

        call = SimpleNamespace(
            data={"entity_id": "lawn_mower.worx", "runtime": 30, "edge_cut": False, "zones": []}
        )
        with self.assertRaises(ServiceValidationError) as ctx:
            await hass.services.handlers[key](call)
        self.assertEqual(ctx.exception.translation_key, "integration_not_loaded")
        self.assertEqual(ctx.exception.translation_placeholders["state"], "setup_retry")

    async def test_service_rejects_other_entities(self) -> None:
        entry = FakeEntry()
        hass = self._hass(entry)
        await INTEGRATION.async_setup(hass, {})
        handler = hass.services.handlers[
            (CONST.DOMAIN, CONST.SERVICE_START_ONE_TIME_MOWING)
        ]
        call = SimpleNamespace(
            data={"entity_id": "switch.other", "runtime": 30, "edge_cut": False, "zones": []}
        )
        with self.assertRaises(ServiceValidationError) as ctx:
            await handler(call)
        self.assertEqual(ctx.exception.translation_key, "invalid_mower_entity")

    async def test_service_reaches_loaded_coordinator(self) -> None:
        entry = FakeEntry()
        hass = self._hass(entry)
        await INTEGRATION.async_setup(hass, {})
        received: list[tuple[Any, ...]] = []

        class Coordinator:
            data = {SERIAL: object()}

            async def async_start_one_time_mowing(self, *args) -> None:
                received.append(args)

        entry.runtime_data = INTEGRATION.WorxVisionRuntimeData(
            cloud=None, coordinator=Coordinator()
        )
        entry.state = ha_stubs.ConfigEntryState.LOADED
        handler = hass.services.handlers[
            (CONST.DOMAIN, CONST.SERVICE_START_ONE_TIME_MOWING)
        ]
        await handler(
            SimpleNamespace(
                data={"entity_id": "lawn_mower.worx", "runtime": 30, "edge_cut": True, "zones": [2]}
            )
        )
        self.assertEqual(received, [(SERIAL, 30, True, [2])])


class RawOptionTests(unittest.TestCase):
    """Raw-entity option stored by the options flow (I10)."""

    def test_options_take_precedence_over_entry_data(self) -> None:
        entry = FakeEntry()
        self.assertFalse(CONST.raw_entities_enabled(entry))
        entry.data = {"expose_raw_entities": True}
        self.assertTrue(CONST.raw_entities_enabled(entry))
        entry.options = {"expose_raw_entities": False}
        self.assertFalse(CONST.raw_entities_enabled(entry))


if __name__ == "__main__":
    unittest.main()
