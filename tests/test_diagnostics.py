"""Tests for diagnostics redaction and sensitive raw paths."""

from __future__ import annotations

import json
from types import SimpleNamespace
import unittest

import ha_stubs

DIAGNOSTICS = ha_stubs.load("diagnostics")
HELPERS = ha_stubs.load("helpers")

SERIAL = "202412345678"
SECRETS = (
    "user@example.com", "hunter2", SERIAL, "uuid-secret", "AA:BB:CC:DD:EE:FF",
    "54.1234567", "18.7654321", "mqtt.secret.example", "token-secret", "user-77",
)


class FakeCoordinator:
    last_update_success = True

    def __init__(self, data) -> None:
        self.data = data

    def maintenance_summary(self, serial_number):
        return {"blade_service_due": False, "serial_number": serial_number}


def _device():
    return SimpleNamespace(
        model="WR308E",
        protocol=1,
        online=True,
        serial_number=SERIAL,
        mac_address="AA:BB:CC:DD:EE:FF",
        status={"id": 1, "description": "home"},
        error={"id": 0},
        battery={"percent": 100},
        raw_cfg={"sn": SERIAL, "rtk": {"ck": "token-secret", "map": "map-1"}},
        raw_dat={
            "uuid": "uuid-secret",
            "mac": "AA:BB:CC:DD:EE:FF",
            "rtk": {"pos": [54.1234567, 18.7654321], "gps": 4},
            "modules": {"4G": {"geo": {"coo": [54.1234567, 18.7654321]}}},
        },
        _worx_vision_product_item={
            "serial_number": SERIAL,
            "mqtt_endpoint": "mqtt.secret.example",
            "user_id": "user-77",
            "lawn_size": 168,
        },
        _worx_vision_rtk_map={
            "layers": {"boundaries": [{"zones": [{"id": 1, "area": 168, "contours": [{"points": [[54.1, 18.7]]}]}]}]}
        },
    )


class DiagnosticsTests(unittest.IsolatedAsyncioTestCase):
    async def test_private_data_is_redacted(self) -> None:
        entry = SimpleNamespace(
            title="Worx Vision Cloud (user@example.com)",
            data={"email": "user@example.com", "password": "hunter2", "cloud": "worx"},
            options={"expose_raw_entities": False},
            runtime_data=SimpleNamespace(
                coordinator=FakeCoordinator({SERIAL: _device()}),
                cloud=SimpleNamespace(mqtt_connected=True),
            ),
        )
        result = await DIAGNOSTICS.async_get_config_entry_diagnostics(None, entry)
        dumped = json.dumps(result, default=str)
        for secret in SECRETS:
            self.assertNotIn(secret, dumped, secret)
        mower = result["mowers"][0]
        self.assertEqual(mower["model"], "WR308E")
        self.assertEqual(mower["raw_cfg"]["rtk"]["map"], "map-1")
        self.assertEqual(mower["product_item"]["lawn_size"], 168)
        self.assertEqual(mower["rtk_map"], {"available": True, "boundaries": 1, "zones": [{"id": 1, "area": 168}]})
        self.assertEqual(result["entry"]["data"]["cloud"], "worx")
        self.assertTrue(result["cloud"]["mqtt_connected"])


class SensitiveRawPathTests(unittest.TestCase):
    def test_location_paths_are_sensitive(self) -> None:
        for path in ("cfg.rtk.ck", "dat.rtk.pos[0]", "dat.rtk.pos.count",
                     "dat.modules.4G.geo.coo[1]", "dat.gps.latitude", "cfg.lon"):
            with self.subTest(path=path):
                self.assertTrue(HELPERS.raw_path_is_sensitive(path))

    def test_ordinary_paths_stay_available(self) -> None:
        for path in ("dat.battery.p", "dat.rtk.gps", "cfg.rtk.map", "dat.ls", "cfg.sc.slots[0].s"):
            with self.subTest(path=path):
                self.assertFalse(HELPERS.raw_path_is_sensitive(path))

    def test_raw_entities_do_not_include_position(self) -> None:
        values = HELPERS.raw_entity_values(_device())
        dumped = json.dumps(values, default=str)
        self.assertNotIn("54.1234567", dumped)
        self.assertIn("map-1", dumped)


if __name__ == "__main__":
    unittest.main()
