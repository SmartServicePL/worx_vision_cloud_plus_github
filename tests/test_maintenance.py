"""Tests for configurable maintenance intervals (issue #6)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
import unittest

import ha_stubs

HELPERS = ha_stubs.load("helpers")
NOW = datetime(2026, 10, 11, 12, tzinfo=UTC)


def _device(**product_item) -> SimpleNamespace:
    return SimpleNamespace(_worx_vision_product_item=product_item)


class MaintenanceDueTests(unittest.TestCase):
    """Blade and battery decisions are independent of each other."""

    def test_both_services_can_be_due_at_once(self) -> None:
        blade, battery = HELPERS.maintenance_due(
            720, 500, None, blade_hours=12, battery_cycles_limit=500,
            blade_days=0, now=NOW,
        )
        self.assertTrue(blade)
        self.assertTrue(battery)

    def test_below_thresholds(self) -> None:
        blade, battery = HELPERS.maintenance_due(
            719, 499, None, blade_hours=12, battery_cycles_limit=500,
            blade_days=0, now=NOW,
        )
        self.assertFalse(blade)
        self.assertFalse(battery)

    def test_missing_counters_give_none(self) -> None:
        self.assertEqual(
            HELPERS.maintenance_due(
                None, None, None, blade_hours=12, battery_cycles_limit=500,
                blade_days=30, now=NOW,
            ),
            (None, None),
        )

    def test_day_interval_counts_from_blade_reset(self) -> None:
        reset = NOW - timedelta(days=31)
        blade, _ = HELPERS.maintenance_due(
            60, None, reset, blade_hours=100, battery_cycles_limit=500,
            blade_days=30, now=NOW,
        )
        self.assertTrue(blade)
        blade, _ = HELPERS.maintenance_due(
            60, None, reset, blade_hours=100, battery_cycles_limit=500,
            blade_days=0, now=NOW,
        )
        self.assertFalse(blade)


class MaintenanceSummaryTests(unittest.TestCase):
    """The combined state keeps the 1.4.x behaviour with default intervals."""

    def _summary(self, device, blade_hours=12, battery=500, days=0):
        return HELPERS.maintenance_summary(
            device, blade_hours=blade_hours, battery_cycles_limit=battery,
            blade_days=days, now=NOW,
        )

    def test_default_state_matches_previous_thresholds(self) -> None:
        device = _device(blade_work_time=800, blade_work_time_reset=80)
        self.assertEqual(HELPERS.maintenance_state(self._summary(device)), "blade_service_due")
        device = _device(blade_work_time=800, blade_work_time_reset=81)
        self.assertEqual(HELPERS.maintenance_state(self._summary(device)), "ok")

    def test_battery_due_is_visible_while_blade_is_due(self) -> None:
        device = _device(blade_work_time=900, battery_charge_cycles=600)
        summary = self._summary(device)
        self.assertEqual(HELPERS.maintenance_state(summary), "blade_service_due")
        self.assertTrue(summary["blade_service_due"])
        self.assertTrue(summary["battery_service_due"])

    def test_no_counters_give_unknown_state(self) -> None:
        self.assertIsNone(HELPERS.maintenance_state(self._summary(_device())))

    def test_reset_timestamp_is_parsed(self) -> None:
        device = _device(blade_work_time=10, blade_work_time_reset_at="2026-09-01T08:00:00Z")
        summary = self._summary(device, days=30)
        self.assertEqual(summary["blade_runtime_reset_at"], datetime(2026, 9, 1, 8, tzinfo=UTC))
        self.assertTrue(summary["blade_service_due"])


if __name__ == "__main__":
    unittest.main()
