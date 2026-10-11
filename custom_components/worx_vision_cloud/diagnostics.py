"""Diagnostics for Worx Vision Cloud PLUS with private data redacted."""
from __future__ import annotations

from typing import Any

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.core import HomeAssistant

from . import WorxConfigEntry
from .helpers import get_dict_value, get_nested_value

# Keys removed wherever they appear: credentials, account and device
# identifiers, network details and everything that reveals the garden location.
TO_REDACT = {
    "access_token",
    "address",
    "ck",
    "coo",
    "display_name",
    "email",
    "geo",
    "gps",
    "house_number",
    "ip",
    "lat",
    "latitude",
    "lon",
    "longitude",
    "mac",
    "mac_address",
    "mqtt_endpoint",
    "mqtt_topics",
    "password",
    "pos",
    "postcode",
    "refresh_token",
    "road",
    "serial",
    "serial_number",
    "sn",
    "ssid",
    "title",
    "token",
    "unique_id",
    "user_id",
    "uuid",
}


def _rtk_map_summary(device: Any) -> dict[str, Any]:
    """Describe the RTK map without its geometry."""
    map_data = getattr(device, "_worx_vision_rtk_map", None)
    if not isinstance(map_data, dict):
        return {"available": False}
    boundaries = get_nested_value(map_data, "layers", "boundaries", default=[]) or []
    zones = [
        zone
        for boundary in boundaries
        if isinstance(boundary, dict)
        for zone in get_dict_value(boundary, "zones", []) or []
        if isinstance(zone, dict)
    ]
    return {
        "available": True,
        "boundaries": len(boundaries),
        "zones": [
            {"id": get_dict_value(zone, "id"), "area": get_dict_value(zone, "area")}
            for zone in zones
        ],
    }


def _device_diagnostics(coordinator: Any, serial_number: str, device: Any) -> dict[str, Any]:
    """Return the useful, redactable state of one mower."""
    return {
        "model": getattr(device, "model", None),
        "protocol": getattr(device, "protocol", None),
        "online": getattr(device, "online", None),
        "updated": getattr(device, "updated", None),
        "status": getattr(device, "status", None),
        "error": getattr(device, "error", None),
        "battery": getattr(device, "battery", None),
        "rainsensor": getattr(device, "rainsensor", None),
        "firmware": getattr(device, "firmware", None),
        "schedules": getattr(device, "schedules", None),
        "raw_cfg": getattr(device, "raw_cfg", None),
        "raw_dat": getattr(device, "raw_dat", None),
        "product_item": getattr(device, "_worx_vision_product_item", None),
        "firmware_upgrade": getattr(device, "_worx_vision_firmware_upgrade", None),
        "rtk_map": _rtk_map_summary(device),
        "maintenance": coordinator.maintenance_summary(serial_number)
        if hasattr(coordinator, "maintenance_summary")
        else None,
    }


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: WorxConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    del hass
    runtime = entry.runtime_data
    coordinator = runtime.coordinator
    cloud = runtime.cloud
    devices = coordinator.data or {}
    data = {
        "entry": {
            "title": entry.title,
            "data": dict(entry.data),
            "options": dict(entry.options),
        },
        "cloud": {
            "mqtt_connected": getattr(cloud, "mqtt_connected", None),
            "last_update_success": coordinator.last_update_success,
        },
        # Mowers are numbered instead of keyed by serial number.
        "mowers": [
            _device_diagnostics(coordinator, serial_number, device)
            for serial_number, device in devices.items()
        ],
    }
    return async_redact_data(data, TO_REDACT)
