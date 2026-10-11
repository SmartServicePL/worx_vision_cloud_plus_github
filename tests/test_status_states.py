"""Every mower state reported by pyworxcloud has a translated status sensor state."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
import unittest

import ha_stubs

SENSOR = ha_stubs.load("sensor")
TRANSLATIONS = Path(__file__).resolve().parents[1] / "custom_components" / "worx_vision_cloud" / "translations"

# pyworxcloud 6.4.2 utils/state.py STATE_TO_DESCRIPTION, without "unknown" and "debug".
LIBRARY_DESCRIPTIONS = {
    0: "idle", 1: "home", 2: "start sequence", 3: "leaving home", 4: "follow wire", 5: "searching home",
    6: "searching wire", 7: "mowing", 8: "mowing", 9: "trapped", 10: "blade blocked", 12: "remote control",
    13: "digital fence escape", 30: "going home", 31: "zoning", 32: "cutting edge", 33: "searching area",
    34: "pause", 103: "searching zone", 104: "searching home", 110: "border crossing", 111: "exploring lawn",
}


def _device(description: str):
    return SimpleNamespace(status={"id": 0, "description": description},
                           error={"id": 0, "description": "no error"}, rainsensor={})


class StatusStateTests(unittest.TestCase):
    def test_every_library_state_maps_to_an_option(self) -> None:
        for state_id, description in LIBRARY_DESCRIPTIONS.items():
            with self.subTest(state_id=state_id, description=description):
                state = SENSOR._status_state(_device(description))
                self.assertIn(state, SENSOR.STATUS_STATE_OPTIONS)

    def test_searching_home_is_its_own_state(self) -> None:
        self.assertEqual(SENSOR._status_state(_device("searching home")), "searching_home")

    def test_error_sensor_keeps_its_mapping(self) -> None:
        device = SimpleNamespace(status={}, error={"id": 9, "description": "trapped"}, rainsensor={})
        self.assertIsNone(SENSOR._error_state(device))

    def test_every_option_is_translated_in_every_language(self) -> None:
        for path in sorted(TRANSLATIONS.glob("*.json")):
            sensors = json.loads(path.read_text(encoding="utf-8"))["entity"]["sensor"]
            for sensor in ("status", "error"):
                states = sensors[sensor]["state"]
                missing = [option for option in SENSOR.STATUS_STATE_OPTIONS if not states.get(option)]
                with self.subTest(language=path.stem, sensor=sensor):
                    self.assertEqual(missing, [])


if __name__ == "__main__":
    unittest.main()
