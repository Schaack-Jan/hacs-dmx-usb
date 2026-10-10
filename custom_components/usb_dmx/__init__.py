"""USB DMX integration."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.config_entries import ConfigEntry, ConfigEntryNotReady
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryError

from .backends.base import BackendError, DmxBackend
from .backends.serial_pro import SerialProBackend
from .const import (
    BACKEND_SERIAL_PRO,
    CONF_BACKEND,
    CONF_BLACKOUT_ON_SHUTDOWN,
    CONF_DEVICE,
    CONF_STARTUP_BEHAVIOR,
    DEFAULT_BLACKOUT_ON_SHUTDOWN,
    DEFAULT_STARTUP_BEHAVIOR,
)
from .controller import DmxController
from .dimmer import DimmerRuntime
from .models import FixtureType, StartupBehavior
from .settings import FadeSettings

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PLATFORMS = (Platform.LIGHT, Platform.NUMBER)


@dataclass(slots=True)
class UsbDmxRuntime:
    """Own the backend and controller for one configured interface."""

    controller: DmxController
    backend: DmxBackend
    startup_behavior: StartupBehavior
    settings: FadeSettings | None = None
    dimmers: dict[str, DimmerRuntime] = field(default_factory=dict)


type UsbDmxConfigEntry = ConfigEntry[UsbDmxRuntime]


class BackendConfigEntry(Protocol):
    """Describe the stored data required to construct a backend."""

    data: Mapping[str, Any]


async def async_create_backend(entry: BackendConfigEntry) -> DmxBackend:
    """Create the configured backend without opening its transport."""
    if entry.data[CONF_BACKEND] != BACKEND_SERIAL_PRO:
        msg = f"Unsupported USB DMX backend: {entry.data[CONF_BACKEND]}"
        raise ValueError(msg)
    return SerialProBackend(entry.data[CONF_DEVICE])


async def async_setup_entry(hass: HomeAssistant, entry: UsbDmxConfigEntry) -> bool:
    """Set up one USB DMX interface config entry."""
    # Import after module initialization because entity platform helpers use this type.
    from .entity import fixtures_from_entry  # noqa: PLC0415

    settings = FadeSettings(hass, entry)
    try:
        _ = settings.global_fade_time  # Validate before opening hardware.
        fixtures = fixtures_from_entry(entry)
        startup_behavior = StartupBehavior(
            entry.options.get(CONF_STARTUP_BEHAVIOR, DEFAULT_STARTUP_BEHAVIOR)
        )
    except ValueError as err:
        raise ConfigEntryError(str(err)) from err
    backend = await async_create_backend(entry)
    controller = DmxController(backend)
    dimmers = {
        fixture.fixture_id: DimmerRuntime(controller, fixture, settings)
        for _, fixture in fixtures
        if fixture.fixture_type is FixtureType.DIMMER
    }

    try:
        await controller.async_start()
        await controller.async_send_current_frame()
    except BackendError as err:
        for dimmer in dimmers.values():
            await dimmer.async_remove(blackout=False)
        await controller.async_stop()
        raise ConfigEntryNotReady from err
    if not controller.available:
        for dimmer in dimmers.values():
            await dimmer.async_remove(blackout=False)
        await controller.async_stop()
        msg = "Unable to send initial DMX zero frame"
        raise ConfigEntryNotReady(msg)

    entry.runtime_data = UsbDmxRuntime(
        controller=controller,
        backend=backend,
        startup_behavior=startup_behavior,
        settings=settings,
        dimmers=dimmers,
    )
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        for dimmer in dimmers.values():
            await dimmer.async_remove(blackout=False)
        await controller.async_stop()
        del entry.runtime_data
        raise

    return True


async def async_unload_entry(hass: HomeAssistant, entry: UsbDmxConfigEntry) -> bool:
    """Unload platforms, then stop and close the shared runtime."""
    if not await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        return False

    if runtime := getattr(entry, "runtime_data", None):
        for dimmer in runtime.dimmers.values():
            await dimmer.async_remove(blackout=False)
        await runtime.controller.async_stop(
            blackout=entry.options.get(
                CONF_BLACKOUT_ON_SHUTDOWN, DEFAULT_BLACKOUT_ON_SHUTDOWN
            )
        )
    return True
