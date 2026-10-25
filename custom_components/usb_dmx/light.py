"""Light platform for single-channel dimmers and fixed-order color fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar, override

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_RGB_COLOR,
    ATTR_RGBW_COLOR,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)

from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from . import UsbDmxConfigEntry
from .const import DMX_MAX_VALUE
from .entity import (
    UsbDmxEntity,
    fixtures_from_entry,
    get_fade_settings,
    get_interface_device,
)
from .models import FixtureConfig, FixtureType, StartupBehavior
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
    lights = [
        (subentry, fixture)
        for subentry, fixture in fixtures
        if fixture.fixture_type in (FixtureType.DIMMER, FixtureType.RGB, FixtureType.RGBW)
    ]
    if not lights:
        return

    device = get_interface_device(hass, entry)
    for subentry, fixture in lights:
        entity = (
            UsbDmxDimmer(fixture, entry)
            if fixture.fixture_type is FixtureType.DIMMER
            else UsbDmxColorLight(fixture, entry)
        )
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

class UsbDmxColorLight(UsbDmxEntity, LightEntity, RestoreEntity):
    """Control a fixed-order RGB or RGBW fixture as one Home Assistant light."""

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Keep logical color and brightness independent of the wire frame."""
        super().__init__(fixture, entry)
        self._attr_color_mode = (
            ColorMode.RGB if fixture.fixture_type is FixtureType.RGB else ColorMode.RGBW
        )
        self._attr_supported_color_modes = {self._attr_color_mode}
        self._color: tuple[int, ...] = (
            (DMX_MAX_VALUE, DMX_MAX_VALUE, DMX_MAX_VALUE)
            if fixture.fixture_type is FixtureType.RGB
            else (DMX_MAX_VALUE, DMX_MAX_VALUE, DMX_MAX_VALUE, 0)
        )
        self._brightness = DMX_MAX_VALUE
        self._is_on = False

    @property
    @override
    def is_on(self) -> bool:
        """Report the logical commanded output state."""
        return self._is_on

    @property
    @override
    def brightness(self) -> int | None:
        """Keep the requested brightness available to the Home Assistant UI."""
        return self._brightness if self._is_on else None

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Expose the requested RGB color in RGB mode only."""
        if self.fixture.fixture_type is FixtureType.RGB:
            return self._color  # type: ignore[return-value]
        return None

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Expose the requested RGBW color in RGBW mode only."""
        if self.fixture.fixture_type is FixtureType.RGBW:
            return self._color  # type: ignore[return-value]
        return None

    def _validated_color(self, value: object) -> tuple[int, ...]:
        """Reject malformed input before changing any fixture channels."""
        if (
            not isinstance(value, (tuple, list))
            or len(value) != self.fixture.channel_count
            or any(type(channel) is not int or not 0 <= channel <= DMX_MAX_VALUE
                   for channel in value)
        ):
            msg = "Color must contain exactly the fixture's 8-bit channels"
            raise ValueError(msg)
        return tuple(value)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Render all color channels as one atomic DMX frame."""
        async with self._command_removal_lock:
            if self._removing:
                return
            brightness = kwargs.get(ATTR_BRIGHTNESS, self._brightness)
            if type(brightness) is not int or not 0 <= brightness <= DMX_MAX_VALUE:
                msg = "Brightness must be an integer between 0 and 255"
                raise ValueError(msg)
            key = (
                ATTR_RGB_COLOR
                if self.fixture.fixture_type is FixtureType.RGB
                else ATTR_RGBW_COLOR
            )
            other_key = (
                ATTR_RGBW_COLOR if key == ATTR_RGB_COLOR else ATTR_RGB_COLOR
            )
            if other_key in kwargs:
                msg = "Color data does not match the fixture's channel mode"
                raise ValueError(msg)
            color = (
                self._validated_color(kwargs[key])
                if key in kwargs
                else self._color
            )
            values = {
                address: round(channel * brightness / DMX_MAX_VALUE)
                for address, channel in zip(
                    self.fixture.occupied_addresses, color, strict=True
                )
            }
            await self.controller.async_set_channels(values)
            self._color = color
            self._brightness = brightness
            self._is_on = brightness > 0
            self._async_write_state_if_added()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Black out every owned channel but remember the next-on color."""
        async with self._command_removal_lock:
            if self._removing:
                return
            await self.controller.async_set_channels(
                dict.fromkeys(self.fixture.occupied_addresses, 0)
            )
            self._is_on = False
            self._async_write_state_if_added()

    @override
    async def async_added_to_hass(self) -> None:
        """Restore the last valid color and optionally its output at startup."""
        await super().async_added_to_hass()
        if self.entry.runtime_data.startup_behavior is not StartupBehavior.RESTORE:
            return
        state = await self.async_get_last_state()
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            return
        key = (
            ATTR_RGB_COLOR
            if self.fixture.fixture_type is FixtureType.RGB
            else ATTR_RGBW_COLOR
        )
        try:
            if (saved_color := state.attributes.get(key)) is not None:
                self._color = self._validated_color(saved_color)
            brightness = state.attributes.get(ATTR_BRIGHTNESS)
            if type(brightness) is int and 1 <= brightness <= DMX_MAX_VALUE:
                self._brightness = brightness
        except ValueError:
            return
        if state.state == STATE_ON:
            await self.async_turn_on()
