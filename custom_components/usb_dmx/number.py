"""Native normalized, fade duration and raw channel Number controls."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, override

from homeassistant.components.number import NumberDeviceClass, NumberEntity, NumberMode
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.core import callback

from . import UsbDmxConfigEntry
from .const import DMX_MAX_VALUE
from .entity import (
    UsbDmxDimmerEntity,
    UsbDmxEntity,
    fixtures_from_entry,
    get_fade_settings,
    get_interface_device,
)
from .models import FixtureConfig, FixtureType
from .restore import UsbDmxRestoringDimmer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up parent settings and owned dimmer or raw fixture controls."""
    fixtures = fixtures_from_entry(entry)
    get_fade_settings(hass, entry)
    device = get_interface_device(hass, entry)
    global_fade = UsbDmxGlobalFadeTime(entry)
    global_fade.device_entry = device
    async_add_entities([global_fade], config_subentry_id=None)
    for subentry, fixture in fixtures:
        entities = (
            [UsbDmxLevel(fixture, entry), UsbDmxFadeTime(fixture, entry)]
            if fixture.fixture_type is FixtureType.DIMMER
            else [UsbDmxRawChannel(fixture, entry)]
            if fixture.fixture_type is FixtureType.RAW
            else []
        )
        if fixture.fixture_type not in (FixtureType.DIMMER, FixtureType.RAW):
            color = entry.runtime_data.get_color(fixture)
            entities.extend(
                UsbDmxColorChannel(fixture, entry, role)
                for role in color.extra_roles()
            )
        for entity in entities:
            entity.device_entry = device
            async_add_entities([entity], config_subentry_id=subentry.subentry_id)


class UsbDmxRawChannel(UsbDmxEntity, NumberEntity):
    """Expose one DMX slot as an unscaled integer number."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = DMX_MAX_VALUE
    _attr_native_step = 1

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Initialize one controller-backed raw channel."""
        super().__init__(fixture, entry)

    @property
    @override
    def native_value(self) -> int:
        """Read the integer slot value from the controller's frame."""
        return self.controller.current_frame[self.fixture.address - 1]

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Validate an integral value and write it through the controller."""
        async with self._command_removal_lock:
            if self._removing:
                return
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                msg = "value must be an integer between 0 and 255"
                raise TypeError(msg)
            if isinstance(value, float) and (
                not math.isfinite(value) or not value.is_integer()
            ):
                msg = "value must be an integer between 0 and 255"
                raise ValueError(msg)
            integer_value = int(value)
            if not 0 <= integer_value <= DMX_MAX_VALUE:
                msg = "value must be an integer between 0 and 255"
                raise ValueError(msg)
            await self.controller.async_set_channel(self.fixture.address, integer_value)
            self._async_write_state_if_added()


class UsbDmxLevel(UsbDmxRestoringDimmer, NumberEntity):
    """Expose a regular normalized fader over the shared logical brightness."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = 1
    _attr_native_step = 0.01
    _attr_translation_key = "level"
    _attr_has_entity_name = True

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Preserve the fixture name through role translation placeholders."""
        super().__init__(fixture, entry)
        self._attr_unique_id += "_level"
        del self._attr_name
        self._attr_translation_placeholders = {"fixture_name": fixture.name}

    @property
    @override
    def native_value(self) -> float:
        """Read the light's published brightness rounded onto the fader display."""
        return self.dimmer.level

    @property
    @override
    def _visible_state(self) -> float:
        """Only publish a changed normalized value."""
        return self.native_value

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Accept off-grid input without quantizing before brightness conversion."""
        if not self._removing:
            await self.dimmer.async_set_level(value)


class UsbDmxFadeTime(UsbDmxDimmerEntity, NumberEntity):
    """Persist an individual duration independently from its source choice."""

    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 2
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "fade_time"
    _attr_has_entity_name = True
    _subscribe_availability = False

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Assign a role-specific identity with the same subentry runtime."""
        super().__init__(fixture, entry)
        self._attr_unique_id += "_fade_time"
        del self._attr_name
        self._attr_translation_placeholders = {"fixture_name": fixture.name}

    @property
    @override
    def available(self) -> bool:
        """Configuration remains editable during a transport outage."""
        return True

    @property
    @override
    def native_value(self) -> float:
        """Read the individual stored duration, even while using global time."""
        return self.dimmer.fade_time

    @property
    @override
    def _visible_state(self) -> float:
        """Ignore brightness progress and source changes."""
        return self.native_value

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Serialize persistent settings with fixture commands and removal."""
        if not self._removing:
            await self.dimmer.async_set_fade_time(value)


class UsbDmxGlobalFadeTime(NumberEntity):
    """Parent-owned persistent global duration, including an empty interface."""

    _attr_should_poll = False
    _attr_mode = NumberMode.BOX
    _attr_native_min_value = 0
    _attr_native_max_value = 2
    _attr_native_step = 0.01
    _attr_native_unit_of_measurement = UnitOfTime.SECONDS
    _attr_device_class = NumberDeviceClass.DURATION
    _attr_entity_category = EntityCategory.CONFIG
    _attr_translation_key = "global_fade_time"
    _attr_has_entity_name = True

    def __init__(self, entry: UsbDmxConfigEntry) -> None:
        """Attach the current interface settings without output restoration."""
        self.settings = entry.runtime_data.settings
        self._attr_unique_id = f"{entry.entry_id}_global_fade_time"
        self._last_visible_value = self.native_value
        self._removing = False

    @property
    @override
    def native_value(self) -> float:
        """Return the current global duration."""
        return self.settings.global_fade_time

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe to only relevant persistent settings changes."""
        await super().async_added_to_hass()
        self.async_on_remove(self.settings.add_listener(self._settings_changed))

    @callback
    def _settings_changed(self) -> None:
        """Deduplicate fixture-only updates."""
        if self.native_value != self._last_visible_value:
            self._last_visible_value = self.native_value
            self.async_write_ha_state()

    @override
    async def async_will_remove_from_hass(self) -> None:
        """Prevent late direct calls after the parent entity is unloaded."""
        self._removing = True
        await super().async_will_remove_from_hass()

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Persist current global settings without DMX or reload."""
        if not self._removing:
            await self.settings.async_set_global_fade_time(value)

class UsbDmxColorChannel(UsbDmxEntity, NumberEntity):
    """Expose the physical W, Amber, UV or Strobe value as a native slider."""

    _attr_mode = NumberMode.SLIDER
    _attr_native_min_value = 0
    _attr_native_max_value = DMX_MAX_VALUE
    _attr_native_step = 1
    _attr_has_entity_name = True

    def __init__(
        self, fixture: FixtureConfig, entry: UsbDmxConfigEntry, role: str
    ) -> None:
        """Use one stable number entity per independent physical channel."""
        super().__init__(fixture, entry)
        self.color = entry.runtime_data.get_color(fixture)
        if role not in self.color.extra_roles():
            raise ValueError("Unsupported color channel")
        self.role = role
        self._attr_unique_id += f"_{role}"
        self._attr_translation_key = f"{role}_channel"
        self._attr_translation_placeholders = {"fixture_name": fixture.name}
        del self._attr_name

    @property
    @override
    def native_value(self) -> int:
        """Read the current raw channel value from the shared color mixer."""
        return self.color.extra(self.role)

    @override
    async def async_added_to_hass(self) -> None:
        """Publish slider changes initiated by either a palette or color picker."""
        await super().async_added_to_hass()
        self.async_on_remove(self.color.add_listener(self._async_write_state_if_added))

    @override
    async def async_set_native_value(self, value: float) -> None:
        """Apply 8-bit input, rejecting floats that are not whole bytes."""
        if self._removing:
            return
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise ValueError("Channel value must be an integer from 0 to 255")
        if not math.isfinite(value) or not float(value).is_integer():
            raise ValueError("Channel value must be an integer from 0 to 255")
        await self.color.set_extra(self.role, int(value))
