"""Tests for translated error keys and one-time mowing validation."""

from __future__ import annotations

import json
from pathlib import Path
import re
from types import SimpleNamespace
import unittest

import ha_stubs

COORDINATOR = ha_stubs.load("coordinator")

from homeassistant.exceptions import ServiceValidationError  # noqa: E402

PACKAGE_DIR = Path(__file__).parents[1] / "custom_components" / "worx_vision_cloud"
SERIAL = "202412345678"


class OnlineCloud:
    mqtt_connected = True

    def __init__(self) -> None:
        self.mqtt = SimpleNamespace(published=[])

    def get_mower(self, serial_number):
        return {"online": True, "protocol": 1, "uuid": "u", "mqtt_topics": {"command_in": "in"}}

    def set_callback(self, event, callback) -> None:
        return None


def _coordinator():
    entry = SimpleNamespace(entry_id="entry-1", data={}, options={}, async_start_reauth=lambda hass: None)
    coordinator = COORDINATOR.WorxVisionCoordinator(SimpleNamespace(), OnlineCloud(), entry)
    coordinator.data = {SERIAL: SimpleNamespace(serial_number=SERIAL)}
    return coordinator


class OneTimeMowingValidationTests(unittest.IsolatedAsyncioTestCase):
    async def test_runtime_zero_needs_edge_cutting(self) -> None:
        with self.assertRaises(ServiceValidationError) as ctx:
            await _coordinator().async_start_one_time_mowing(SERIAL, 0, False, [])
        self.assertEqual(ctx.exception.translation_key, "runtime_required")

    async def test_edge_only_pass_cannot_use_zones(self) -> None:
        with self.assertRaises(ServiceValidationError) as ctx:
            await _coordinator().async_start_one_time_mowing(SERIAL, 0, True, [2])
        self.assertEqual(ctx.exception.translation_key, "edge_only_without_zones")


class TranslationKeyTests(unittest.TestCase):
    def test_every_error_key_is_translated_in_every_language(self) -> None:
        used = set()
        for path in PACKAGE_DIR.glob("*.py"):
            used.update(re.findall(r'translation_key="([a-z0-9_]+)"', path.read_text(encoding="utf-8")))
        entity_keys = set()
        english = json.loads((PACKAGE_DIR / "translations" / "en.json").read_text(encoding="utf-8"))
        for platform in english.get("entity", {}).values():
            entity_keys.update(platform)
        exception_keys = used - entity_keys - {"schedule", "firmware", "rtk_position"}
        self.assertTrue({"runtime_required", "library_feature_unsupported"} <= exception_keys)
        for path in sorted((PACKAGE_DIR / "translations").glob("*.json")):
            data = json.loads(path.read_text(encoding="utf-8"))
            exceptions = data.get("exceptions", {})
            with self.subTest(language=path.stem):
                self.assertEqual(sorted(exception_keys - set(exceptions)), [])
                english_placeholders = {
                    key: sorted(re.findall(r"\{(\w+)\}", english["exceptions"][key]["message"]))
                    for key in english["exceptions"]
                }
                for key, value in exceptions.items():
                    self.assertEqual(
                        sorted(re.findall(r"\{(\w+)\}", value["message"])),
                        english_placeholders[key],
                        key,
                    )

    def test_no_untranslated_library_messages_remain(self) -> None:
        for path in PACKAGE_DIR.glob("*.py"):
            text = path.read_text(encoding="utf-8")
            with self.subTest(file=path.name):
                self.assertNotIn("does not support", text)


if __name__ == "__main__":
    unittest.main()
