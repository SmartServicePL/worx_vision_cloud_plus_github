"""Tests for releasing the previous MQTT client before reconnecting."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
import unittest

import ha_stubs

COORDINATOR = ha_stubs.load("coordinator")


class FakeEntry:
    entry_id = "entry-1"
    title = "Worx"
    data: dict = {}
    options: dict = {}

    def async_start_reauth(self, hass) -> None:
        return None


class ReconnectCloud:
    """WorxCloud stand-in that records the order of reconnect steps."""

    def __init__(self) -> None:
        self.mqtt_connected = False
        self.mqtt = object()
        self.events: list[str] = []
        self.disconnect_error: Exception | None = None
        self._mqtt_retry_task = None

    async def _disconnect_mqtt(self, logger) -> None:
        self.events.append("disconnect_mqtt")
        try:
            if self.disconnect_error is not None:
                raise self.disconnect_error
        finally:
            self.mqtt = None

    async def connect(self) -> None:
        self.events.append(f"connect(old_client={'yes' if self.mqtt is not None else 'no'})")
        self.mqtt = object()
        self.mqtt_connected = True

    def set_callback(self, event, callback) -> None:
        return None


def _coordinator(cloud: ReconnectCloud):
    coordinator = COORDINATOR.WorxVisionCoordinator(SimpleNamespace(), cloud, FakeEntry())
    coordinator.data = {}
    return coordinator


class ReconnectTests(unittest.IsolatedAsyncioTestCase):
    """connect() in pyworxcloud 6.4.2 does not stop the previous MQTT client."""

    async def test_previous_client_is_stopped_before_connect(self) -> None:
        cloud = ReconnectCloud()
        await _coordinator(cloud)._async_reconnect()
        self.assertEqual(cloud.events, ["disconnect_mqtt", "connect(old_client=no)"])

    async def test_library_retry_task_is_cancelled(self) -> None:
        cloud = ReconnectCloud()

        async def retry_loop() -> None:
            await asyncio.sleep(3600)

        cloud._mqtt_retry_task = asyncio.create_task(retry_loop())
        await asyncio.sleep(0)
        task = cloud._mqtt_retry_task
        await _coordinator(cloud)._async_reconnect()
        self.assertTrue(task.cancelled())
        self.assertIsNone(cloud._mqtt_retry_task)

    async def test_without_previous_client_only_connects(self) -> None:
        cloud = ReconnectCloud()
        cloud.mqtt = None
        await _coordinator(cloud)._async_reconnect()
        self.assertEqual(cloud.events, ["connect(old_client=no)"])

    async def test_failing_disconnect_does_not_block_reconnect(self) -> None:
        cloud = ReconnectCloud()
        cloud.disconnect_error = RuntimeError("socket already closed")
        await _coordinator(cloud)._async_reconnect()
        self.assertEqual(cloud.events, ["disconnect_mqtt", "connect(old_client=no)"])
        self.assertTrue(cloud.mqtt_connected)

    async def test_connected_client_is_left_alone(self) -> None:
        cloud = ReconnectCloud()
        cloud.mqtt_connected = True
        await _coordinator(cloud)._async_reconnect()
        self.assertEqual(cloud.events, [])

    async def test_library_without_private_helper_still_connects(self) -> None:
        cloud = ReconnectCloud()
        cloud._disconnect_mqtt = None
        await _coordinator(cloud)._async_reconnect()
        self.assertEqual(cloud.events, ["connect(old_client=yes)"])


if __name__ == "__main__":
    unittest.main()
