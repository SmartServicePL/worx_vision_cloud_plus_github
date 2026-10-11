"""Constants for Worx Vision Cloud Plus."""
from __future__ import annotations

from typing import Any

from homeassistant.const import Platform

try:
    from homeassistant.const import UnitOfRatio
except ImportError:  # Home Assistant before 2026.7
    PERCENTAGE_UNIT = "%"
else:
    PERCENTAGE_UNIT = UnitOfRatio.PERCENTAGE

DOMAIN = "worx_vision_cloud"

CONF_CLOUD = "cloud"
CONF_VERIFY_SSL = "verify_ssl"
CONF_EXPOSE_RAW = "expose_raw_entities"

DEFAULT_CLOUD = "worx"
DEFAULT_VERIFY_SSL = True
DEFAULT_EXPOSE_RAW = False

CLOUDS = ["worx", "kress", "landxcape"]

PLATFORMS = [
    Platform.LAWN_MOWER,
    Platform.CALENDAR,
    Platform.CAMERA,
    Platform.DEVICE_TRACKER,
    Platform.SENSOR,
    Platform.SELECT,
    Platform.BINARY_SENSOR,
    Platform.SWITCH,
    Platform.NUMBER,
    Platform.BUTTON,
    Platform.UPDATE,
]

ATTR_RAW_PATH = "raw_path"
ATTR_RAW_SOURCE = "raw_source"
ATTR_SERIAL_NUMBER = "serial_number"

SERVICE_START_ONE_TIME_MOWING = "start_one_time_mowing"

ATTR_EDGE_CUT = "edge_cut"
ATTR_RUNTIME = "runtime"
ATTR_ZONES = "zones"

# Local, per-mower maintenance intervals (number entities). The defaults keep
# the thresholds used before they became configurable: 720 blade minutes (12 h)
# and 500 battery charge cycles; the day-based blade interval is off (0).
MAINTENANCE_BLADE_HOURS = "blade_hours"
MAINTENANCE_BATTERY_CYCLES = "battery_cycles"
MAINTENANCE_BLADE_DAYS = "blade_days"
MAINTENANCE_DEFAULTS = {
    MAINTENANCE_BLADE_HOURS: 12,
    MAINTENANCE_BATTERY_CYCLES: 500,
    MAINTENANCE_BLADE_DAYS: 0,
}
MAINTENANCE_LIMITS = {
    MAINTENANCE_BLADE_HOURS: (1, 500),
    MAINTENANCE_BATTERY_CYCLES: (50, 3000),
    MAINTENANCE_BLADE_DAYS: (0, 365),
}


def raw_entities_enabled(entry: Any) -> bool:
    """Return whether raw payload entities are enabled for a config entry.

    The options flow stores the choice in entry.options. Entries created
    before the options flow existed keep their original choice from entry.data.
    """
    if CONF_EXPOSE_RAW in entry.options:
        return bool(entry.options[CONF_EXPOSE_RAW])
    return bool(entry.data.get(CONF_EXPOSE_RAW, DEFAULT_EXPOSE_RAW))
