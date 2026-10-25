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
from homeassistant.const import STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from . import UsbDmxConfigEntry
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
    """Set up dimmers and native color lights with exact subentry owners."""
    fixtures = fixtures_from_entry(entry)
    get_fade_settings(hass, entry)
    lights = [
        (subentry, fixture)
        for subentry, fixture in fixtures
        if fixture.fixture_type is not FixtureType.RAW
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
        if fixture.fixture_type is FixtureType.LED_BAR:
            for pixel in range(fixture.pixel_count):
                child = UsbDmxColorLight(fixture, entry, pixel=pixel)
                child.device_entry = device
                async_add_entities([child], config_subentry_id=subentry.subentry_id)


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
    """Expose an RGB or RGBW picker backed by a shared fixture color mixer."""

    def __init__(
        self,
        fixture: FixtureConfig,
        entry: UsbDmxConfigEntry,
        *,
        pixel: int | None = None,
    ) -> None:
        """Assign a stable ID to the group or one addressable LED bar pixel."""
        super().__init__(fixture, entry)
        self.color = entry.runtime_data.get_color(fixture)
        self.pixel = pixel
        self._attr_color_mode = ColorMode.RGBW if self.color.is_rgbw else ColorMode.RGB
        self._attr_supported_color_modes = {self._attr_color_mode}
        if pixel is not None:
            self._attr_unique_id += f"_pixel_{pixel + 1}"
            self._attr_name = f"{fixture.name} Pixel {pixel + 1}"

    @property
    @override
    def is_on(self) -> bool:
        """Read the current mixer state, including pixel-only updates."""
        return self.color.get_state(self.pixel)[2]

    @property
    @override
    def brightness(self) -> int | None:
        """Read the shared logical brightness when the requested pixel is on."""
        _, brightness, enabled = self.color.get_state(self.pixel)
        return brightness if enabled else None

    @property
    @override
    def rgb_color(self) -> tuple[int, int, int] | None:
        """Expose free RGB mixing through Home Assistant's native color wheel."""
        if self.color.is_rgbw:
            return None
        channels = self.color.get_state(self.pixel)[0]
        return channels[0], channels[1], channels[2]

    @property
    @override
    def rgbw_color(self) -> tuple[int, int, int, int] | None:
        """Expose RGBW mixing, including the physical white channel."""
        if not self.color.is_rgbw:
            return None
        channels = self.color.get_state(self.pixel)[0]
        return channels[0], channels[1], channels[2], channels[3]

    @property
    @override
    def extra_state_attributes(self) -> dict[str, int] | None:
        """Publish separately adjustable W/A/UV/Strobe settings on the group."""
        if self.pixel is not None:
            return None
        return {role: self.color.extra_value(role) for role in self.color.extra_roles()}

    @override
    async def async_added_to_hass(self) -> None:
        """Share mixer updates; only the fixture group restores saved state."""
        await super().async_added_to_hass()
        self.async_on_remove(self.color.add_listener(self._async_write_state_if_added))
        if self.pixel is not None:
            return
        if self.entry.runtime_data.startup_behavior is not StartupBehavior.RESTORE:
            return
        previous = await self.async_get_last_state()
        if previous is None or previous.state != STATE_ON:
            return
        key = ATTR_RGBW_COLOR if self.color.is_rgbw else ATTR_RGB_COLOR
        stored_color = previous.attributes.get(key)
        stored_level = previous.attributes.get(ATTR_BRIGHTNESS, 255)
        if stored_color is None or type(stored_level) is not int:
            return
        try:
            await self.color.restore(
                stored_color,
                stored_level,
                {
                    role: previous.attributes.get(role, 0)
                    for role in self.color.extra_roles()
                    if role != "strobe" and role in previous.attributes
                },
            )
        except ValueError:
            return

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Change color and brightness together without partial DMX writes."""
        if self._removing:
            return
        key = ATTR_RGBW_COLOR if self.color.is_rgbw else ATTR_RGB_COLOR
        other_key = ATTR_RGB_COLOR if self.color.is_rgbw else ATTR_RGBW_COLOR
        if other_key in kwargs:
            msg = "Color payload does not match the fixture mode"
            raise ValueError(msg)
        if ATTR_TRANSITION in kwargs and kwargs[ATTR_TRANSITION] != 0:
            msg = "Color transitions are not yet supported"
            raise ValueError(msg)
        await self.color.set_color(
            color=kwargs.get(key),
            brightness=kwargs.get(ATTR_BRIGHTNESS),
            pixel=self.pixel,
        )

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Send zero to this group or just the selected bar pixel."""
        if self._removing:
            return
        if ATTR_TRANSITION in kwargs and kwargs[ATTR_TRANSITION] != 0:
            msg = "Color transitions are not yet supported"
            raise ValueError(msg)
        await self.color.turn_off(pixel=self.pixel)
