"""Number platform for Worx Vision Cloud Plus."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Awaitable, Callable

from homeassistant.components.number import (
    NumberEntity,
    NumberEntityDescription,
    NumberMode,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import UnitOfArea, UnitOfLength, UnitOfTime
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity import EntityCategory
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .const import DOMAIN, PERCENTAGE_UNIT
from .entity import WorxVisionEntity
from .helpers import get_dict_value, get_nested_value

BORDER_DISTANCE_VALUES_MM = (50, 100, 150, 200)


@dataclass(frozen=True, kw_only=True)
class WorxNumberDescription(NumberEntityDescription):
    """Number description."""

    value_fn: Callable[[Any], float | None]
    set_fn: Callable[[Any, str, float], Awaitable[None]]
    available_fn: Callable[[Any], bool] | None = None
    attrs_fn: Callable[[Any], dict[str, Any] | None] | None = None


def _as_float(value: Any) -> float | None:
    """Return a float from API scalar values."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _rain(device, key, default=None):
    return get_dict_value(getattr(device, "rainsensor", {}), key, default)


def _schedule_value(device, key, default=None):
    return get_dict_value(getattr(device, "schedules", {}) or {}, key, default)


def _product_item(device, key, default=None):
    return get_dict_value(
        getattr(device, "_worx_vision_product_item", {}) or {}, key, default
    )


def _rtk_map_data(device) -> dict[str, Any]:
    value = getattr(device, "_worx_vision_rtk_map", {}) or {}
    return value if isinstance(value, dict) else {}


def _raw_cfg(device) -> dict[str, Any]:
    value = getattr(device, "raw_cfg", {}) or {}
    return value if isinstance(value, dict) else {}


def _first_raw_zone_cutting(device) -> dict[str, Any]:
    raw_cfg = _raw_cfg(device)
    for zone_path in (("rtk", "zs"), ("mz", "s")):
        zones = get_nested_value(raw_cfg, *zone_path, default=[]) or []
        if not isinstance(zones, list | tuple):
            continue
        for zone in zones:
            cutting = get_nested_value(zone, "cfg", "cut", default={}) or {}
            if isinstance(cutting, dict) and cutting:
                return cutting
    return {}


def _first_map_zone(device) -> dict[str, Any]:
    layers = get_dict_value(_rtk_map_data(device), "layers", {}) or {}
    boundaries = get_dict_value(layers, "boundaries", []) or []
    for boundary in boundaries:
        zones = get_dict_value(boundary, "zones", []) or []
        for zone in zones:
            if isinstance(zone, dict):
                return zone
    return {}


def _lawn_area(device) -> float | None:
    value = _as_float(_product_item(device, "lawn_size"))
    if value is not None and value > 0:
        return round(value)

    map_area = _as_float(get_dict_value(_first_map_zone(device), "area"))
    if map_area is None:
        return None
    return round(map_area / 1_000_000)


def _lawn_perimeter(device) -> float | None:
    value = _as_float(_product_item(device, "lawn_perimeter"))
    if value is not None and value > 0:
        return round(value)

    map_perimeter = _as_float(get_dict_value(_first_map_zone(device), "perimeter"))
    if map_perimeter is None:
        return None
    return round(map_perimeter / 1000)


def _has_capability(device, capability: str) -> bool:
    """Return true when product item capabilities include a value."""
    product_item = getattr(device, "_worx_vision_product_item", {}) or {}
    capabilities = get_dict_value(product_item, "capabilities", []) or []
    return isinstance(capabilities, list | tuple) and capability in capabilities


def _is_online(device) -> bool:
    """Return true when the mower can receive commands."""
    return bool(getattr(device, "online", False))


def _has_time_extension(device) -> bool:
    """Return true when schedule time extension is visible."""
    return _is_online(device) and _schedule_value(device, "time_extension") is not None


def _border_distance(device) -> float | None:
    """Return configured Vision border-cut distance in millimeters."""
    zone_cutting = _first_raw_zone_cutting(device)
    candidates = (
        get_nested_value(_raw_cfg(device), "cut", "bd"),
        get_nested_value(_raw_cfg(device), "cut", "co"),
        get_dict_value(zone_cutting, "bd"),
        get_dict_value(zone_cutting, "co"),
        _product_item(device, "border_distance"),
        _product_item(device, "border_cut_distance"),
    )
    for candidate in candidates:
        value = _as_float(candidate)
        if value is not None and value > 0:
            return value
    return None


async def _set_rain_delay(coordinator, serial_number: str, value: float) -> None:
    await coordinator.async_set_rain_delay(serial_number, round(value))


async def _set_time_extension(coordinator, serial_number: str, value: float) -> None:
    await coordinator.async_set_time_extension(serial_number, round(value))


async def _set_lawn_area(coordinator, serial_number: str, value: float) -> None:
    await coordinator.async_set_lawn_size(serial_number, round(value))


async def _set_lawn_perimeter(coordinator, serial_number: str, value: float) -> None:
    await coordinator.async_set_lawn_perimeter(serial_number, round(value))


async def _set_border_distance(coordinator, serial_number: str, value: float) -> None:
    closest_value = min(
        BORDER_DISTANCE_VALUES_MM,
        key=lambda allowed: abs(allowed - float(value)),
    )
    await coordinator.async_set_border_distance(serial_number, closest_value)


NUMBERS: tuple[WorxNumberDescription, ...] = (
    WorxNumberDescription(
        key="rain_delay_minutes",
        translation_key="rain_delay_minutes",
        icon="mdi:weather-pouring",
        entity_category=EntityCategory.CONFIG,
        native_min_value=0,
        native_max_value=300,
        native_step=1,
        native_unit_of_measurement=UnitOfTime.MINUTES,
        mode=NumberMode.BOX,
        value_fn=lambda d: _as_float(_rain(d, "delay")),
        set_fn=_set_rain_delay,
        available_fn=lambda d: _is_online(d) and _has_capability(d, "rain_delay"),
        attrs_fn=lambda d: {
            "triggered": _rain(d, "triggered"),
            "remaining": _rain(d, "remaining"),
            "api_method": "pyworxcloud.raindelay",
        },
    ),
    WorxNumberDescription(
        key="time_extension",
        translation_key="time_extension",
        icon="mdi:clock-plus-outline",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        native_min_value=-100,
        native_max_value=100,
        native_step=1,
        native_unit_of_measurement=PERCENTAGE_UNIT,
        mode=NumberMode.BOX,
        value_fn=lambda d: _as_float(_schedule_value(d, "time_extension")),
        set_fn=_set_time_extension,
        available_fn=_has_time_extension,
        attrs_fn=lambda d: {
            "api_method": "pyworxcloud.set_time_extension",
            "schedule_active": _schedule_value(d, "active"),
        },
    ),
    WorxNumberDescription(
        key="lawn_area",
        translation_key="lawn_area",
        icon="mdi:set-square",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        native_min_value=1,
        native_max_value=10000,
        native_step=1,
        native_unit_of_measurement=UnitOfArea.SQUARE_METERS,
        mode=NumberMode.BOX,
        value_fn=_lawn_area,
        set_fn=_set_lawn_area,
        available_fn=_is_online,
        attrs_fn=lambda d: {
            "api_method": "pyworxcloud.set_lawn_size",
            "product_item_value": _product_item(d, "lawn_size"),
            "map_zone_value": get_dict_value(_first_map_zone(d), "area"),
        },
    ),
    WorxNumberDescription(
        key="lawn_perimeter",
        translation_key="lawn_perimeter",
        icon="mdi:vector-polyline",
        entity_category=EntityCategory.CONFIG,
        entity_registry_enabled_default=False,
        native_min_value=1,
        native_max_value=5000,
        native_step=1,
        native_unit_of_measurement=UnitOfLength.METERS,
        mode=NumberMode.BOX,
        value_fn=_lawn_perimeter,
        set_fn=_set_lawn_perimeter,
        available_fn=_is_online,
        attrs_fn=lambda d: {
            "api_method": "pyworxcloud.set_lawn_perimeter",
            "product_item_value": _product_item(d, "lawn_perimeter"),
            "map_zone_value": get_dict_value(_first_map_zone(d), "perimeter"),
        },
    ),
    WorxNumberDescription(
        key="border_distance",
        translation_key="border_distance",
        icon="mdi:border-outside",
        entity_category=EntityCategory.CONFIG,
        native_min_value=50,
        native_max_value=200,
        native_step=50,
        native_unit_of_measurement="mm",
        mode=NumberMode.BOX,
        value_fn=_border_distance,
        set_fn=_set_border_distance,
        available_fn=lambda d: _is_online(d) and _has_capability(d, "border_cut"),
        attrs_fn=lambda d: {
            "api_method": "pyworxcloud.set_border_distance",
            "allowed_values_mm": list(BORDER_DISTANCE_VALUES_MM),
            "raw_top_level_border_distance": get_nested_value(
                _raw_cfg(d), "cut", "bd"
            ),
            "raw_top_level_cut_offset": get_nested_value(_raw_cfg(d), "cut", "co"),
            "raw_zone_border_distance": get_dict_value(
                _first_raw_zone_cutting(d), "bd"
            ),
            "raw_zone_cut_offset": get_dict_value(_first_raw_zone_cutting(d), "co"),
        },
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up number entities."""
    runtime = hass.data[DOMAIN][entry.entry_id]
    entities: list[NumberEntity] = [
        WorxVisionNumber(runtime.coordinator, entry, serial_number, description)
        for serial_number in runtime.coordinator.data
        for description in NUMBERS
    ]
    entities.extend(
        OneTimeMowingRuntimeNumber(runtime.coordinator, entry, serial_number)
        for serial_number in runtime.coordinator.data
    )
    async_add_entities(entities)


class WorxVisionNumber(WorxVisionEntity, NumberEntity):
    """Writable mower setting represented as a number."""

    entity_description: WorxNumberDescription

    def __init__(
        self,
        coordinator,
        entry,
        serial_number: str,
        description: WorxNumberDescription,
    ) -> None:
        """Initialize number."""
        self.entity_description = description
        super().__init__(coordinator, entry, serial_number, description.key)

    @property
    def available(self) -> bool:
        """Return entity availability."""
        available_fn = self.entity_description.available_fn
        return (
            super().available
            and self.native_value is not None
            and (available_fn is None or available_fn(self.device))
        )

    @property
    def native_value(self) -> float | None:
        """Return current value."""
        return self.entity_description.value_fn(self.device)

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra attributes."""
        if self.entity_description.attrs_fn is None:
            return None
        attrs = self.entity_description.attrs_fn(self.device)
        return {
            key: value for key, value in (attrs or {}).items() if value is not None
        }

    async def async_set_native_value(self, value: float) -> None:
        """Set a new numeric value."""
        await self.entity_description.set_fn(
            self.coordinator, self._serial_number, value
        )


class OneTimeMowingRuntimeNumber(WorxVisionEntity, NumberEntity):
    """Local runtime setting for one-time mowing."""

    _attr_translation_key = "one_time_mowing_runtime"
    _attr_icon = "mdi:timer-play-outline"
    _attr_native_min_value = 10
    _attr_native_max_value = 120
    _attr_native_step = 5
    _attr_native_unit_of_measurement = UnitOfTime.MINUTES
    _attr_mode = NumberMode.SLIDER

    def __init__(self, coordinator, entry, serial_number: str) -> None:
        """Initialize one-time mowing runtime number."""
        super().__init__(coordinator, entry, serial_number, "one_time_mowing_runtime")

    @property
    def native_value(self) -> float | None:
        """Return configured runtime."""
        return self.coordinator.one_time_mowing_runtime(self._serial_number)

    async def async_set_native_value(self, value: float) -> None:
        """Set configured runtime."""
        await self.coordinator.async_set_one_time_mowing_runtime(
            self._serial_number, round(value)
        )
