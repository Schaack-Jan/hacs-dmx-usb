"""Select platform for persistent USB DMX dimmer fade sources."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar, override

from homeassistant.components.select import SelectEntity
from homeassistant.const import EntityCategory

from . import UsbDmxConfigEntry
from .entity import (
    UsbDmxDimmerEntity,
    UsbDmxEntity,
    fixtures_from_entry,
    get_fade_settings,
    get_interface_device,
)
from .models import FixtureConfig, FixtureType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Add one source selector for each dimmer with its exact subentry owner."""
    fixtures = fixtures_from_entry(entry)
    get_fade_settings(hass, entry)
    device = get_interface_device(hass, entry)
    for subentry, fixture in fixtures:
        if fixture.fixture_type is FixtureType.DIMMER:
            entity = UsbDmxFadeSource(fixture, entry)
        elif fixture.fixture_type is not FixtureType.RAW:
            entity = UsbDmxColorPalette(fixture, entry)
        else:
            continue
        entity.device_entry = device
        async_add_entities([entity], config_subentry_id=subentry.subentry_id)


class UsbDmxFadeSource(UsbDmxDimmerEntity, SelectEntity):
    """Choose global or retained individual timing for future commands."""

    _attr_options: ClassVar[list[str]] = ["global", "individual"]
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "fade_source"
    _attr_has_entity_name = True
    _subscribe_availability = False

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Assign source role identity while retaining the fixture's given name."""
        super().__init__(fixture, entry)
        self._attr_unique_id += "_fade_source"
        del self._attr_name
        self._attr_translation_placeholders = {"fixture_name": fixture.name}

    @property
    @override
    def available(self) -> bool:
        """Keep settings available while USB is disconnected."""
        return True

    @property
    @override
    def current_option(self) -> str:
        """Read the exact persisted stable source value."""
        return self.dimmer.fade_source.value

    @property
    @override
    def _visible_state(self) -> str:
        """Ignore output progress and unrelated duration changes."""
        return self.current_option

    @override
    async def async_select_option(self, option: str) -> None:
        """Persist a validated source under common removal coordination."""
        if not self._removing:
            await self.dimmer.async_set_fade_source(option)


class UsbDmxColorPalette(UsbDmxEntity, SelectEntity):
    """Provide reusable colors without replacing free color-wheel mixing."""

    _attr_translation_key = "palette"
    _attr_has_entity_name = True

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Keep one palette for the entire fixture, including grouped pixel bars."""
        super().__init__(fixture, entry)
        self.color = entry.runtime_data.get_color(fixture)
        self._attr_options = self.color.palette_options
        self._attr_unique_id += "_palette"
        self._attr_translation_placeholders = {"fixture_name": fixture.name}
        del self._attr_name

    @property
    @override
    def current_option(self) -> str:
        """Return 'custom' when a user adjusts any individual color component."""
        return self.color.palette

    @override
    async def async_added_to_hass(self) -> None:
        """Keep the select state synchronized with manual mixer commands."""
        await super().async_added_to_hass()
        self.async_on_remove(self.color.add_listener(self._async_write_state_if_added))

    @override
    async def async_select_option(self, option: str) -> None:
        """Apply one predefined color using a single complete DMX frame."""
        if not self._removing:
            await self.color.apply_palette(option)
