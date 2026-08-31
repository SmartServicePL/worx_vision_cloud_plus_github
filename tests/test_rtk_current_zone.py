"""Tests for RTK current-zone detection."""

from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
import sys
import unittest


HOMEASSISTANT = ModuleType("homeassistant")
HOMEASSISTANT_UTIL = ModuleType("homeassistant.util")
HOMEASSISTANT_UTIL.slugify = lambda value: str(value).lower().replace(" ", "_")
HOMEASSISTANT.util = HOMEASSISTANT_UTIL
sys.modules.setdefault("homeassistant", HOMEASSISTANT)
sys.modules.setdefault("homeassistant.util", HOMEASSISTANT_UTIL)

MODULE_PATH = (
    Path(__file__).parents[1] / "custom_components" / "worx_vision_cloud" / "helpers.py"
)
SPEC = importlib.util.spec_from_file_location(
    "worx_helpers_rtk_under_test", MODULE_PATH
)
assert SPEC is not None and SPEC.loader is not None
HELPERS = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(HELPERS)


def _device(
    position: list[float], legacy_current: int | str | None = 0
) -> SimpleNamespace:
    """Return a mower-like object with RTK position and map geometry."""
    return SimpleNamespace(
        raw_cfg={"rtk": {"map": "map-1"}},
        raw_dat={"rtk": {"pos": position}},
        zone={"current": legacy_current},
        _worx_vision_rtk_map={
            "layers": {
                "boundaries": [
                    {
                        "zones": [
                            {
                                "id": 1,
                                "name": "Front lawn",
                                "contours": [
                                    {
                                        "points": [
                                            [52.0000, 20.0000],
                                            [52.0000, 20.0100],
                                            [52.0100, 20.0100],
                                            [52.0100, 20.0000],
                                        ],
                                        "children": [
                                            {
                                                "points": [
                                                    [52.0040, 20.0040],
                                                    [52.0040, 20.0060],
                                                    [52.0060, 20.0060],
                                                    [52.0060, 20.0040],
                                                ],
                                            }
                                        ],
                                    }
                                ],
                            },
                            {
                                "id": 2,
                                "contours": [
                                    {
                                        "points": [
                                            [52.0200, 20.0200],
                                            [52.0200, 20.0300],
                                            [52.0300, 20.0300],
                                            [52.0300, 20.0200],
                                        ],
                                    }
                                ],
                            },
                        ]
                    }
                ]
            }
        },
    )


class RtkCurrentZoneTests(unittest.TestCase):
    """Exercise current-zone geometry lookup."""

    def test_current_zone_name_uses_zone_containing_position(self) -> None:
        self.assertEqual(
            HELPERS.rtk_current_zone_name(_device([52.002, 20.002])),
            "Front lawn",
        )

    def test_current_zone_ignores_hole_children(self) -> None:
        self.assertIsNone(HELPERS.rtk_current_zone_name(_device([52.005, 20.005])))

    def test_current_zone_falls_back_to_zone_id_when_name_missing(self) -> None:
        self.assertEqual(
            HELPERS.rtk_current_zone_name(_device([52.022, 20.022])), "Zone 2"
        )

    def test_current_zone_returns_none_without_map_or_position(self) -> None:
        self.assertIsNone(
            HELPERS.rtk_current_zone_name(
                SimpleNamespace(raw_dat={}, _worx_vision_rtk_map={})
            )
        )

    def test_rtk_zone_zero_uses_live_polygon_zone(self) -> None:
        self.assertEqual(
            HELPERS.current_zone_value(_device([52.002, 20.002], 0)),
            "Front lawn",
        )

    def test_rtk_zone_zero_is_empty_outside_lawn(self) -> None:
        self.assertIsNone(
            HELPERS.current_zone_value(_device([52.050, 20.050], 0))
        )

    def test_legacy_zone_zero_is_preserved_without_rtk(self) -> None:
        device = SimpleNamespace(raw_cfg={}, raw_dat={}, zone={"current": 0})
        self.assertEqual(HELPERS.current_zone_value(device), 0)


if __name__ == "__main__":
    unittest.main()
