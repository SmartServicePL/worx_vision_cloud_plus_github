"""Tests for the periodic refresh interval and the library API refresh event."""

from __future__ import annotations

from datetime import timedelta
from types import SimpleNamespace
import unittest

import ha_stubs

COORDINATOR = ha_stubs.load("coordinator")

from pyworxcloud import LandroidEvent  # noqa: E402


class CallbackCloud:
    """WorxCloud stand-in that keeps the registered callbacks."""

    def __init__(self) -> None:
        self.callbacks = {}
        self.mqtt_connected = True

    def set_callback(self, event, callback) -> None:
        self.callbacks[event] = callback


class FakeLoop:
    def call_soon_threadsafe(self, callback, *args) -> None:
        callback(*args)


class RefreshIntervalTests(unittest.IsolatedAsyncioTestCase):
    async def test_periodic_refresh_is_only_a_fallback(self) -> None:
        self.assertEqual(COORDINATOR.COORDINATOR_UPDATE_INTERVAL, timedelta(minutes=15))
        self.assertEqual(COORDINATOR.PRODUCT_ITEM_CACHE_TTL, timedelta(minutes=5))

    async def test_library_api_event_requests_a_refresh(self) -> None:
        created = []
        hass = SimpleNamespace(loop=FakeLoop(), async_create_task=lambda coro: created.append(coro))
        cloud = CallbackCloud()
        entry = SimpleNamespace(entry_id="entry-1", data={}, options={})
        coordinator = COORDINATOR.WorxVisionCoordinator(hass, cloud, entry)
        self.assertEqual(coordinator.update_interval, timedelta(minutes=15))
        await coordinator.async_setup()
        cloud.callbacks[LandroidEvent.API](api_data={"name": "x"})
        self.assertEqual(len(created), 1)
        await created[0]
        self.assertEqual(coordinator.refresh_requests, 1)


if __name__ == "__main__":
    unittest.main()
