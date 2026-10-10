"""Light platform for USB DMX dimmer fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, override

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)

from . import UsbDmxConfigEntry
from .entity import fixtures_from_entry, get_fade_settings, get_interface_device
from .models import FixtureType
from .restore import UsbDmxRestoringDimmer

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up every dimmer fixture with its exact subentry owner."""
    fixtures = fixtures_from_entry(entry)
    get_fade_settings(hass, entry)
    dimmers = [
        (subentry, fixture)
        for subentry, fixture in fixtures
        if fixture.fixture_type is FixtureType.DIMMER
    ]
    if not dimmers:
        return

    device = get_interface_device(hass, entry)
    for subentry, fixture in dimmers:
        entity = UsbDmxDimmer(fixture, entry)
        entity.device_entry = device
        async_add_entities([entity], config_subentry_id=subentry.subentry_id)


class UsbDmxDimmer(UsbDmxRestoringDimmer, LightEntity):
    """Expose shared live logical brightness as the existing native light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    _attr_supported_features = LightEntityFeature.TRANSITION

    @property
    @override
    def is_on(self) -> bool:
        """Stay on throughout fade-off until logical output reaches exact zero."""
        return self.dimmer.is_on

    @property
    @override
    def brightness(self) -> int | None:
        """Read current output rather than the requested target."""
        return self.dimmer.brightness

    @property
    @override
    def state_attributes(self) -> dict[str, Any]:
        """Expose no stale brightness attribute at the exact off endpoint."""
        attributes = super().state_attributes
        if not self.is_on:
            attributes.pop(ATTR_BRIGHTNESS, None)
        return attributes

    @property
    @override
    def _visible_state(self) -> tuple[bool, int | None]:
        """Publish only changed visible brightness or on/off state."""
        return self.is_on, self.brightness

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Use the shared command path and preserve explicit zero transition."""
        if not self._removing:
            if ATTR_TRANSITION in kwargs:
                self.dimmer._validated_number(  # noqa: SLF001
                    kwargs[ATTR_TRANSITION], 6553, "transition"
                )
            await self.dimmer.async_turn_on(
                kwargs.get(ATTR_BRIGHTNESS), transition=kwargs.get(ATTR_TRANSITION)
            )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Fade out through the same logical state used by the companion fader."""
        if not self._removing:
            if ATTR_TRANSITION in kwargs:
                self.dimmer._validated_number(  # noqa: SLF001
                    kwargs[ATTR_TRANSITION], 6553, "transition"
                )
            await self.dimmer.async_turn_off(transition=kwargs.get(ATTR_TRANSITION))
