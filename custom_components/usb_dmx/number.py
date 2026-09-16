"""Number platform for USB DMX raw-channel fixtures."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, override

from homeassistant.components.number import NumberEntity, NumberMode

from . import UsbDmxConfigEntry
from .const import DMX_MAX_VALUE
from .entity import UsbDmxEntity, fixtures_from_entry, get_interface_device
from .models import FixtureConfig, FixtureType

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up every raw fixture with its exact subentry owner."""
    fixtures = fixtures_from_entry(entry)
    raw_channels = [
        (subentry, fixture)
        for subentry, fixture in fixtures
        if fixture.fixture_type is FixtureType.RAW
    ]
    if not raw_channels:
        return

    device = get_interface_device(hass, entry)
    for subentry, fixture in raw_channels:
        entity = UsbDmxRawChannel(fixture, entry)
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
