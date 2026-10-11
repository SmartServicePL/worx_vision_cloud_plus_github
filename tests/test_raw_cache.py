"""Tests for computing raw entity values once per coordinator update."""

from __future__ import annotations

from types import SimpleNamespace
import unittest
from unittest import mock

import ha_stubs

COORDINATOR = ha_stubs.load("coordinator")
SERIAL = "202412345678"


class Cloud:
    mqtt_connected = True

    def set_callback(self, event, callback) -> None:
        return None


class RawCacheTests(unittest.TestCase):
    def setUp(self) -> None:
        entry = SimpleNamespace(entry_id="entry-1", data={}, options={})
        self.coordinator = COORDINATOR.WorxVisionCoordinator(SimpleNamespace(), Cloud(), entry)
        self.device = SimpleNamespace(raw_dat={"battery": {"p": 80}}, raw_cfg={})
        self.coordinator.data = {SERIAL: self.device}

    def test_payload_is_flattened_once_per_update(self) -> None:
        with mock.patch.object(COORDINATOR, "raw_entity_values", wraps=COORDINATOR.raw_entity_values) as flatten:
            for _ in range(50):
                self.coordinator.raw_values(SERIAL)
            self.assertEqual(flatten.call_count, 1)
            self.device.raw_dat["battery"]["p"] = 79
            self.coordinator.async_update_listeners()
            values = self.coordinator.raw_values(SERIAL)
            self.assertEqual(flatten.call_count, 2)
        self.assertIn(79, values.values())

    def test_unknown_mower_has_no_values(self) -> None:
        self.assertEqual(self.coordinator.raw_values("other"), {})


if __name__ == "__main__":
    unittest.main()
