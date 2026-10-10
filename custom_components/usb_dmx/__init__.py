"""USB DMX integration."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Protocol

from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntry,
    ConfigEntryChange,
    ConfigEntryNotReady,
)
from homeassistant.const import Platform
from homeassistant.core import callback
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers import entity_platform as ep
from homeassistant.helpers.dispatcher import async_dispatcher_connect

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
    DOMAIN,
)
from .controller import DmxController
from .dimmer import DimmerRuntime
from .models import FixtureConfig, FixtureType, StartupBehavior
from .settings import FadeSettings

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

PLATFORMS = (Platform.LIGHT, Platform.NUMBER, Platform.SELECT)


@dataclass(slots=True)
class UsbDmxRuntime:
    """Own the backend and controller for one configured interface."""

    controller: DmxController
    backend: DmxBackend
    startup_behavior: StartupBehavior
    settings: FadeSettings | None = None
    dimmers: dict[str, DimmerRuntime] = field(default_factory=dict)

    def get_dimmer(self, fixture: FixtureConfig) -> DimmerRuntime:
        """Get the common fixture runtime, including manually constructed entries."""
        if fixture.fixture_id not in self.dimmers:
            self.dimmers[fixture.fixture_id] = DimmerRuntime(
                self.controller, fixture, self.settings
            )
        return self.dimmers[fixture.fixture_id]


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
    runtime = UsbDmxRuntime(controller, backend, startup_behavior, settings)
    for _, fixture in fixtures:
        if fixture.fixture_type is FixtureType.DIMMER:
            runtime.get_dimmer(fixture)
    dimmers = runtime.dimmers

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

    entry.runtime_data = runtime
    from .entity import cleanup_obsolete_entities  # noqa: PLC0415

    cleanup_obsolete_entities(hass, entry, fixtures)

    unsubscribe_removal = _subscribe_fixture_removal(hass, entry)
    previous_platforms = set(ep.async_get_platforms(hass, DOMAIN))
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
        _validate_forwarded_platforms(hass, entry, previous_platforms)
    except BaseException:
        unsubscribe_removal()
        for dimmer in dimmers.values():
            await dimmer.async_remove(blackout=False)
        try:
            await _async_cleanup_failed_platforms(hass, entry, previous_platforms)
        finally:
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


@callback
def _subscribe_fixture_removal(
    hass: HomeAssistant, entry: UsbDmxConfigEntry
) -> Callable[[], None]:
    """Observe deletion without interfering with automatic flow reloads."""
    runtime = entry.runtime_data

    @callback
    def entry_changed(change: ConfigEntryChange, changed: ConfigEntry) -> None:
        """Clean up removed runtimes even when every companion is disabled."""
        if change is ConfigEntryChange.UPDATED and changed is entry:
            present = {subentry.unique_id for subentry in entry.subentries.values()}
            for fixture_id, dimmer in runtime.dimmers.items():
                if fixture_id not in present:
                    hass.async_create_task(dimmer.async_remove(blackout=True))

    remove_listener = async_dispatcher_connect(
        hass, SIGNAL_CONFIG_ENTRY_CHANGED, entry_changed
    )

    @callback
    def unsubscribe() -> None:
        """Allow immediate setup-failure cleanup and subsequent HA unload cleanup."""
        nonlocal remove_listener
        if remove_listener is not None:
            remove_listener()
            remove_listener = None

    entry.async_on_unload(unsubscribe)
    return unsubscribe


async def _async_cleanup_failed_platforms(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    previous_platforms: set[ep.EntityPlatform],
) -> None:
    """Unwind newly forwarded platforms before a setup failure closes transport."""
    platforms = {
        platform.domain
        for platform in ep.async_get_platforms(hass, DOMAIN)
        if platform.config_entry is entry and platform not in previous_platforms
    }
    await hass.config_entries.async_unload_platforms(entry, platforms)


@callback
def _validate_forwarded_platforms(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    previous_platforms: set[ep.EntityPlatform],
) -> None:
    """Detect platform-local failures hidden by HA's forwarding API."""
    # HA 2026.9 catches platform setup and entity-addition exceptions. Its
    # completion flag becomes true only after both have succeeded; forwarding
    # itself does not expose the result. Inspect only this attempt's platforms.
    completed = {
        platform.domain
        for platform in ep.async_get_platforms(hass, DOMAIN)
        if platform.config_entry is entry
        and platform not in previous_platforms
        and platform._setup_complete  # noqa: SLF001
    }
    if missing := set(PLATFORMS) - completed:
        message = f"Unable to set up USB DMX platforms: {', '.join(sorted(missing))}"
        raise ConfigEntryNotReady(message)
