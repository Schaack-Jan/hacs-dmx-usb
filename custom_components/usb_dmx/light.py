"""Light platform for USB DMX dimmer fixtures."""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any, ClassVar, Final, override

from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntity,
    LightEntityFeature,
)
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers.restore_state import RestoreEntity

from . import UsbDmxConfigEntry
from .const import DMX_MAX_VALUE
from .entity import UsbDmxEntity, fixtures_from_entry, get_interface_device
from .models import FixtureConfig, FixtureType, StartupBehavior

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

_MAX_TRANSITION_SECONDS: Final = 6553


async def async_setup_entry(
    hass: HomeAssistant,
    entry: UsbDmxConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up every dimmer fixture with its exact subentry owner."""
    fixtures = fixtures_from_entry(entry)
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


class UsbDmxDimmer(UsbDmxEntity, LightEntity, RestoreEntity):
    """Expose one DMX slot as a brightness light."""

    _attr_color_mode = ColorMode.BRIGHTNESS
    _attr_supported_color_modes: ClassVar[set[ColorMode]] = {ColorMode.BRIGHTNESS}
    _attr_supported_features = LightEntityFeature.TRANSITION

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Initialize the light in its deterministic off state."""
        super().__init__(fixture, entry)
        self._attr_is_on = False
        self._attr_brightness: int | None = None

    @override
    async def async_added_to_hass(self) -> None:
        """Restore a valid saved light state only when configured."""
        await super().async_added_to_hass()
        if self.entry.runtime_data.startup_behavior is not StartupBehavior.RESTORE:
            return
        await self._async_restore_last_state()

    async def _async_restore_last_state(self) -> None:
        """Apply one valid saved state without inventing persisted data."""
        state = await self.async_get_last_state()
        if state is None or state.state not in (STATE_ON, STATE_OFF):
            return

        brightness = state.attributes.get(ATTR_BRIGHTNESS)
        if state.state == STATE_OFF:
            if (restored := self._restored_brightness(brightness)) is not None:
                self._attr_brightness = restored or None
            return

        if restored := self._restored_brightness(brightness):
            await self._async_apply_brightness(restored, transition=None)

    @override
    async def async_turn_on(self, **kwargs: Any) -> None:
        """Turn on at an explicit, remembered, or full brightness."""
        brightness = kwargs.get(ATTR_BRIGHTNESS)
        if brightness is None:
            brightness = self._attr_brightness or DMX_MAX_VALUE
        self._validate_brightness(brightness)
        transition = self._validated_transition(kwargs)
        await self._async_apply_brightness(brightness, transition=transition)
        self._async_write_state_if_added()

    @override
    async def async_turn_off(self, **kwargs: Any) -> None:
        """Turn off while preserving the last nonzero HA brightness."""
        transition = self._validated_transition(kwargs)
        self._attr_is_on = False
        await self._async_write_dmx(0, transition=transition)
        self._async_write_state_if_added()

    async def _async_apply_brightness(
        self, brightness: int, *, transition: float | None
    ) -> None:
        """Update state and write a validated HA brightness through the controller."""
        if brightness == 0:
            self._attr_is_on = False
            await self._async_write_dmx(0, transition=transition)
            return

        self._attr_brightness = brightness
        self._attr_is_on = True
        await self._async_write_dmx(
            self._brightness_to_dmx(brightness), transition=transition
        )

    async def _async_write_dmx(self, value: int, *, transition: float | None) -> None:
        """Route one direct or transitioned write through the controller."""
        if transition is None:
            await self.controller.async_set_channel(self.fixture.address, value)
        else:
            await self.controller.async_transition_channel(
                self.fixture.address, value, transition
            )

    def _brightness_to_dmx(self, brightness: int) -> int:
        """Map HA's nonzero brightness interval to the configured DMX range."""
        if brightness == 0:
            return 0
        return round(
            self.fixture.minimum
            + (brightness - 1)
            * (self.fixture.maximum - self.fixture.minimum)
            / (DMX_MAX_VALUE - 1)
        )

    @staticmethod
    def _validate_brightness(brightness: object) -> None:
        """Reject direct-call brightness values outside HA's integer contract."""
        if type(brightness) is not int:
            msg = "brightness must be an integer between 0 and 255"
            raise TypeError(msg)
        if not 0 <= brightness <= DMX_MAX_VALUE:
            msg = "brightness must be an integer between 0 and 255"
            raise ValueError(msg)

    @classmethod
    def _restored_brightness(cls, brightness: object) -> int | None:
        """Return a valid stored brightness, otherwise ignore it."""
        try:
            cls._validate_brightness(brightness)
        except TypeError, ValueError:
            return None
        return brightness

    @staticmethod
    def _validated_transition(kwargs: dict[str, Any]) -> float | None:
        """Return a finite HA transition duration or reject a direct bad call."""
        if ATTR_TRANSITION not in kwargs:
            return None
        transition = kwargs[ATTR_TRANSITION]
        if isinstance(transition, bool) or not isinstance(transition, (int, float)):
            msg = "transition must be a finite number between 0 and 6553"
            raise TypeError(msg)
        if (
            not math.isfinite(transition)
            or not 0 <= transition <= _MAX_TRANSITION_SECONDS
        ):
            msg = "transition must be a finite number between 0 and 6553"
            raise ValueError(msg)
        return float(transition)
