"""Minimal Home Assistant, pyworxcloud and voluptuous stand-ins for unit tests.

The tests run on a plain Python install (as in CI), so the integration package
is imported against these small stubs. They implement only what the tested
code paths use.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum, IntFlag, StrEnum
import importlib
from pathlib import Path
import sys
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).parents[1]
PACKAGE = "worx_vision_cloud"


def _module(name: str) -> ModuleType:
    """Return an existing module or register a new empty one."""
    module = sys.modules.get(name)
    if module is None:
        module = ModuleType(name)
        sys.modules[name] = module
    parent_name, _, child = name.rpartition(".")
    if parent_name:
        setattr(_module(parent_name), child, module)
    return module


def _set_default(module: ModuleType, **attrs: Any) -> None:
    for key, value in attrs.items():
        if not hasattr(module, key):
            setattr(module, key, value)


class HomeAssistantError(Exception):
    """Stand-in for homeassistant.exceptions.HomeAssistantError."""

    def __init__(
        self,
        *args: Any,
        translation_domain: str | None = None,
        translation_key: str | None = None,
        translation_placeholders: dict[str, str] | None = None,
    ) -> None:
        super().__init__(*args)
        self.translation_domain = translation_domain
        self.translation_key = translation_key
        self.translation_placeholders = translation_placeholders


class ServiceValidationError(HomeAssistantError):
    """Stand-in."""


class IntegrationError(HomeAssistantError):
    """Stand-in."""


class ConfigEntryAuthFailed(IntegrationError):
    """Stand-in."""


class ConfigEntryNotReady(IntegrationError):
    """Stand-in."""


class UpdateFailed(HomeAssistantError):
    """Stand-in."""


class DataUpdateCoordinator:
    """Small stand-in keeping the attributes the coordinator uses."""

    def __init__(self, hass, logger, *, config_entry=None, name=None,
                 update_interval=None, always_update=True) -> None:
        self.hass = hass
        self.logger = logger
        self.config_entry = config_entry
        self.name = name
        self.update_interval = update_interval
        self.data = None
        self.last_update_success = True
        self.refresh_requests = 0
        self.listener_updates = 0

    def __class_getitem__(cls, item):
        return cls

    async def async_request_refresh(self) -> None:
        self.refresh_requests += 1

    def async_update_listeners(self) -> None:
        self.listener_updates += 1

    async def async_shutdown(self) -> None:
        return None


class CoordinatorEntity:
    """Stand-in for CoordinatorEntity."""

    def __init__(self, coordinator) -> None:
        self.coordinator = coordinator

    def __class_getitem__(cls, item):
        return cls

    @property
    def available(self) -> bool:
        return self.coordinator.last_update_success


class Store:
    """In-memory stand-in for homeassistant.helpers.storage.Store."""

    def __init__(self, hass, version, key) -> None:
        self.key = key
        self.data: Any = None
        self.saved: Any = None

    def __class_getitem__(cls, item):
        return cls

    async def async_load(self) -> Any:
        return self.data

    async def async_save(self, data: Any) -> None:
        self.saved = data

    def async_delay_save(self, data_func, delay=0) -> None:
        self.saved = data_func()


class ConfigEntryState(Enum):
    LOADED = "loaded"
    SETUP_RETRY = "setup_retry"
    NOT_LOADED = "not_loaded"


class LawnMowerActivity(StrEnum):
    ERROR = "error"
    PAUSED = "paused"
    MOWING = "mowing"
    DOCKED = "docked"
    RETURNING = "returning"


class LawnMowerEntityFeature(IntFlag):
    START_MOWING = 1
    PAUSE = 2
    DOCK = 4


class _Marker:
    """voluptuous Required/Optional stand-in."""

    def __init__(self, key, default=None) -> None:
        self.key = key
        self.default = default

    def __hash__(self) -> int:
        return hash(self.key)


def _install_voluptuous() -> None:
    try:
        importlib.import_module("voluptuous")
        return
    except ImportError:
        pass
    vol = _module("voluptuous")

    class Invalid(Exception):
        pass

    class Schema:
        def __init__(self, schema, *args, **kwargs) -> None:
            self.schema = schema

        def __call__(self, value):
            return value

    _set_default(
        vol,
        Invalid=Invalid,
        Schema=Schema,
        Required=_Marker,
        Optional=_Marker,
        All=lambda *validators: validators[-1],
        Coerce=lambda kind: kind,
        Range=lambda **kwargs: (lambda value: value),
        In=lambda values: (lambda value: value),
    )


def _install_aiohttp() -> None:
    try:
        importlib.import_module("aiohttp")
        return
    except ImportError:
        pass
    aiohttp = _module("aiohttp")

    class ClientError(Exception):
        pass

    class ClientTimeout:
        def __init__(self, total=None) -> None:
            self.total = total

    _set_default(aiohttp, ClientError=ClientError, ClientTimeout=ClientTimeout)


def _install_pyworxcloud() -> None:
    root = _module("pyworxcloud")
    exceptions = _module("pyworxcloud.exceptions")
    for name in (
        "AuthorizationError",
        "TooManyRequestsError",
        "NoConnectionError",
        "TimeoutException",
    ):
        _set_default(exceptions, **{name: type(name, (Exception,), {})})

    class LandroidEvent(Enum):
        DATA_RECEIVED = "data_received"
        API = "api"

    _set_default(
        root,
        DeviceHandler=object,
        WorxCloud=object,
        LandroidEvent=LandroidEvent,
    )
    requests = _module("pyworxcloud.utils.requests")

    async def aget(*args, **kwargs):  # pragma: no cover - patched in tests
        return None

    _set_default(requests, AGET=aget, HEADERS=lambda token: {})


def _install_homeassistant() -> None:
    _module("homeassistant")
    util = _module("homeassistant.util")
    _set_default(util, slugify=lambda value: str(value).lower().replace(" ", "_"))
    dt_util = _module("homeassistant.util.dt")
    _set_default(
        dt_util,
        now=lambda: datetime.now(UTC),
        utcnow=lambda: datetime.now(UTC),
        as_local=lambda value: value,
    )
    util.dt = dt_util

    const = _module("homeassistant.const")

    class Platform(StrEnum):
        LAWN_MOWER = "lawn_mower"
        CALENDAR = "calendar"
        CAMERA = "camera"
        DEVICE_TRACKER = "device_tracker"
        SENSOR = "sensor"
        SELECT = "select"
        BINARY_SENSOR = "binary_sensor"
        SWITCH = "switch"
        NUMBER = "number"
        BUTTON = "button"
        UPDATE = "update"

    _set_default(
        const,
        Platform=Platform,
        ATTR_ENTITY_ID="entity_id",
        CONF_EMAIL="email",
        CONF_PASSWORD="password",
    )

    core = _module("homeassistant.core")
    _set_default(
        core,
        HomeAssistant=object,
        ServiceCall=object,
        callback=lambda func: func,
    )

    exceptions = _module("homeassistant.exceptions")
    _set_default(
        exceptions,
        HomeAssistantError=HomeAssistantError,
        ServiceValidationError=ServiceValidationError,
        ConfigEntryAuthFailed=ConfigEntryAuthFailed,
        ConfigEntryNotReady=ConfigEntryNotReady,
    )

    config_entries = _module("homeassistant.config_entries")
    _set_default(
        config_entries,
        ConfigEntry=object,
        ConfigEntryState=ConfigEntryState,
    )

    helpers = _module("homeassistant.helpers")
    for name in ("area_registry", "device_registry", "entity_registry"):
        _module(f"homeassistant.helpers.{name}")
    _set_default(
        sys.modules["homeassistant.helpers.device_registry"],
        CONNECTION_NETWORK_MAC="mac",
    )
    _set_default(
        sys.modules["homeassistant.helpers.entity_registry"],
        async_get=lambda hass: hass.entity_registry,
        async_entries_for_config_entry=lambda registry, entry_id: [
            item for item in registry.entries.values()
            if item.config_entry_id == entry_id
        ],
    )
    cv = _module("homeassistant.helpers.config_validation")
    _set_default(
        cv,
        entity_id=lambda value: value,
        boolean=bool,
        config_entry_only_config_schema=lambda domain: ("config_entry_only", domain),
    )
    _set_default(_module("homeassistant.helpers.typing"), ConfigType=dict)
    _set_default(
        _module("homeassistant.helpers.aiohttp_client"),
        async_get_clientsession=lambda hass: None,
    )
    _set_default(_module("homeassistant.helpers.storage"), Store=Store)
    _set_default(
        _module("homeassistant.helpers.update_coordinator"),
        DataUpdateCoordinator=DataUpdateCoordinator,
        UpdateFailed=UpdateFailed,
        CoordinatorEntity=CoordinatorEntity,
    )
    _set_default(_module("homeassistant.helpers.entity"), DeviceInfo=dict)
    _set_default(
        _module("homeassistant.helpers.entity_platform"),
        AddConfigEntryEntitiesCallback=object,
    )
    def async_redact_data(data, to_redact):
        """Redact keys recursively, like homeassistant.components.diagnostics."""
        if isinstance(data, list):
            return [async_redact_data(item, to_redact) for item in data]
        if not isinstance(data, dict):
            return data
        return {
            key: "**REDACTED**"
            if key in to_redact
            else async_redact_data(value, to_redact)
            for key, value in data.items()
        }

    _set_default(
        _module("homeassistant.components.diagnostics"),
        async_redact_data=async_redact_data,
    )
    _set_default(
        _module("homeassistant.components.lawn_mower"),
        LawnMowerActivity=LawnMowerActivity,
        LawnMowerEntity=object,
        LawnMowerEntityFeature=LawnMowerEntityFeature,
    )
    del helpers


def install() -> None:
    """Install all stubs and make the integration importable as a package."""
    _install_voluptuous()
    _install_aiohttp()
    _install_pyworxcloud()
    _install_homeassistant()
    custom_components = str(REPO_ROOT / "custom_components")
    if custom_components not in sys.path:
        sys.path.insert(0, custom_components)


def load(module: str) -> ModuleType:
    """Import one integration module (for example 'coordinator')."""
    install()
    return importlib.import_module(f"{PACKAGE}.{module}")
