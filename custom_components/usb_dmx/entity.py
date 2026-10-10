"""Shared entity support for USB DMX fixtures."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING, override

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import UsbDmxConfigEntry
from .const import CONF_DEVICE, DOMAIN, SUBENTRY_TYPE_FIXTURE
from .controller import DmxController
from .models import (
    FixtureConfig,
    FixtureType,
    FixtureValidationError,
    validate_fixtures,
)
from .settings import FadeSettings

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant


def fixture_from_subentry(subentry: ConfigSubentry) -> FixtureConfig:
    """Load one fixture while enforcing its immutable subentry identity."""
    fixture = FixtureConfig.from_mapping(subentry.data)
    if subentry.unique_id != fixture.fixture_id:
        raise FixtureValidationError("invalid_fixture")
    return fixture


def fixtures_from_entry(
    entry: UsbDmxConfigEntry,
) -> list[tuple[ConfigSubentry, FixtureConfig]]:
    """Load and validate the complete stored fixture collection."""
    fixtures = [
        (subentry, fixture_from_subentry(subentry))
        for subentry in entry.get_subentries_of_type(SUBENTRY_TYPE_FIXTURE)
    ]
    validate_fixtures([fixture for _, fixture in fixtures])
    return fixtures


def _interface_name(entry: UsbDmxConfigEntry) -> str:
    """Return a useful device name without leaking a raw serial path."""
    title = entry.title.strip()
    device = entry.data.get(CONF_DEVICE)
    if title and (not isinstance(device, str) or device not in title):
        return title
    return "USB DMX Interface"


def interface_device_info(entry: UsbDmxConfigEntry) -> DeviceInfo:
    """Return stable parent-owned metadata for the physical interface."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="USB DMX",
        model="DMX512 Interface",
        name=_interface_name(entry),
        translation_key="interface",
    )


def get_interface_device(
    hass: HomeAssistant, entry: UsbDmxConfigEntry
) -> dr.DeviceEntry:
    """Get one parent-owned device shared by every fixture subentry."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=None,
        **interface_device_info(entry),
    )


@callback
def get_fade_settings(hass: HomeAssistant, entry: UsbDmxConfigEntry) -> FadeSettings:
    """Initialize fallback runtime settings before any platform creates dimmers."""
    if entry.runtime_data.settings is None:
        entry.runtime_data.settings = FadeSettings(hass, entry)
    return entry.runtime_data.settings


class UsbDmxEntity(Entity):
    """Base entity for one fixture on a shared physical interface."""

    _attr_should_poll = False
    _subscribe_availability = True

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Initialize stable identity and shared runtime ownership."""
        self.fixture = fixture
        self.entry = entry
        self.controller: DmxController = entry.runtime_data.controller
        self._command_removal_lock = asyncio.Lock()
        self._removing = False
        self._attr_name = fixture.name
        self._attr_unique_id = f"{entry.entry_id}_{fixture.fixture_id}"

    @property
    @override
    def available(self) -> bool:
        """Mirror the shared controller's transport connectivity."""
        return self.controller.available

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe once to shared availability changes."""
        await super().async_added_to_hass()
        if self._subscribe_availability:
            self.async_on_remove(
                self.controller.add_availability_listener(
                    self._async_handle_availability_update
                )
            )

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Clear a deleted fixture slot but preserve it during normal unload."""
        await super().async_will_remove_from_hass()
        self._removing = True
        async with self._command_removal_lock:
            if any(
                subentry.unique_id == self.fixture.fixture_id
                for subentry in self.entry.get_subentries_of_type(SUBENTRY_TYPE_FIXTURE)
            ):
                return
            await self.controller.async_set_channel(self.fixture.address, 0)

    @callback
    def _async_handle_availability_update(
        self,
        _available: bool,  # noqa: FBT001
    ) -> None:
        """Publish controller availability when the entity is fully added."""
        self._async_write_state_if_added()

    @callback
    def _async_write_state_if_added(self) -> None:
        """Publish state only while the entity belongs to a live platform."""
        if self.hass is not None and self.platform is not None:
            self.async_write_ha_state()


@callback
def cleanup_obsolete_entities(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    fixtures: list[tuple[ConfigSubentry, FixtureConfig]],
) -> None:
    """Remove only obsolete native roles after a fixture changes type."""
    expected = {("number", f"{entry.entry_id}_global_fade_time")}
    for _, fixture in fixtures:
        base = f"{entry.entry_id}_{fixture.fixture_id}"
        if fixture.fixture_type is FixtureType.DIMMER:
            expected.update(
                {
                    ("light", base),
                    ("number", f"{base}_level"),
                    ("number", f"{base}_fade_time"),
                    ("select", f"{base}_fade_source"),
                }
            )
        else:
            expected.add(("number", base))
    registry = er.async_get(hass)
    for registered in er.async_entries_for_config_entry(registry, entry.entry_id):
        if (
            registered.platform == DOMAIN
            and (registered.domain, registered.unique_id) not in expected
        ):
            registry.async_remove(registered.entity_id)


class UsbDmxDimmerEntity(UsbDmxEntity):
    """Share logical state and terminal deletion across native fixture controls."""

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Attach to one common runtime without issuing any output command."""
        super().__init__(fixture, entry)
        self.dimmer = entry.runtime_data.get_dimmer(fixture)
        self._last_visible_state: object = None

    @property
    def _visible_state(self) -> object:
        """Return the values relevant to this role's HA state."""
        raise NotImplementedError

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe only to visible changes; settings roles avoid availability."""
        await super().async_added_to_hass()
        self._last_visible_state = self._visible_state
        source = self.dimmer if self._subscribe_availability else self.dimmer.settings
        self.async_on_remove(source.add_listener(self._async_handle_dimmer_update))

    @callback
    def _async_handle_dimmer_update(self) -> None:
        """Deduplicate logical ticks and unrelated setting updates for each role."""
        visible = self._visible_state
        if visible != self._last_visible_state:
            self._last_visible_state = visible
            self._async_write_state_if_added()

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Delete the shared fixture once, while an ordinary entity unload is local."""
        self._removing = True
        # Entity's callbacks have been removed by the platform before this hook.
        await Entity.async_will_remove_from_hass(self)
        if not any(
            subentry.unique_id == self.fixture.fixture_id
            for subentry in self.entry.get_subentries_of_type(SUBENTRY_TYPE_FIXTURE)
        ):
            await self.dimmer.async_remove(blackout=True)
