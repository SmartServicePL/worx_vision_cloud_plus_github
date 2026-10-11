"""Regression tests for the mower status and error mapping."""

from __future__ import annotations

from types import SimpleNamespace
import unittest

import ha_stubs

HELPERS = ha_stubs.load("helpers")
LAWN_MOWER = ha_stubs.load("lawn_mower")

from homeassistant.components.lawn_mower import LawnMowerActivity  # noqa: E402


def _mower(status_id: int, error_id: int = 0, error: str = "no error"):
    entity = LAWN_MOWER.WorxVisionLawnMower.__new__(LAWN_MOWER.WorxVisionLawnMower)
    device = SimpleNamespace(
        status={"id": status_id, "description": ""},
        error={"id": error_id, "description": error},
        rainsensor={"triggered": False, "remaining": 0},
        online=True,
    )
    entity.coordinator = SimpleNamespace(data={"sn": device}, last_update_success=True)
    entity._serial_number = "sn"
    return entity


class StatusMappingTests(unittest.TestCase):
    def test_status_8_is_mowing_as_in_pyworxcloud(self) -> None:
        # pyworxcloud 6.4.2 utils/state.py: STATE_TO_DESCRIPTION[8] == "mowing".
        self.assertIn(8, HELPERS.MOWING_STATUS_IDS)
        self.assertNotIn(8, HELPERS.ERROR_STATUS_IDS)
        self.assertEqual(_mower(8).activity, LawnMowerActivity.MOWING)

    def test_lifted_comes_from_error_code_2(self) -> None:
        # ERROR_TO_DESCRIPTION[2] == "lifted": an error id, reported as ERROR.
        self.assertEqual(_mower(7, error_id=2, error="lifted").activity, LawnMowerActivity.ERROR)


if __name__ == "__main__":
    unittest.main()
