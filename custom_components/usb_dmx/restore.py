"""Resolve one fixture restore source independently of active platform order."""

from __future__ import annotations

import math
from typing import Any, override

from homeassistant.components.light import ATTR_BRIGHTNESS
from homeassistant.const import STATE_OFF, STATE_ON
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.restore_state import (
    ExtraStoredData,
    RestoreEntity,
    StoredState,
    async_get,
)

from .const import DMX_MAX_VALUE, DOMAIN
from .entity import UsbDmxDimmerEntity
from .models import StartupBehavior


class DimmerExtraData(ExtraStoredData):
    """Preserve exact current output and requested positive target when off."""

    def __init__(self, brightness: int | None, last_on_brightness: int | None) -> None:
        """Snapshot logical output and separate target memory."""
        self.brightness = brightness or 0
        self.last_on_brightness = last_on_brightness

    @override
    def as_dict(self) -> dict[str, Any]:
        """Return HA-compatible restore data, without the fader's display rounding."""
        return {
            "brightness": self.brightness,
            "last_on_brightness": self.last_on_brightness,
        }


def _restore_values(
    stored: StoredState, *, light: bool
) -> tuple[int | None, int | None] | None:
    """Accept valid saved output, allowing legacy off brightness as target memory."""
    state = stored.state
    saved_extra = stored.extra_data.as_dict() if stored.extra_data else {}
    extra = saved_extra if isinstance(saved_extra, dict) else {}
    remembered = extra.get("last_on_brightness")
    if type(remembered) is not int or not 1 <= remembered <= DMX_MAX_VALUE:
        remembered = None
    if light:
        brightness = state.attributes.get(ATTR_BRIGHTNESS)
        if state.state == STATE_OFF:
            if (
                remembered is None
                and type(brightness) is int
                and 1 <= brightness <= DMX_MAX_VALUE
            ):
                remembered = brightness
            return 0, remembered
        if (
            state.state == STATE_ON
            and type(brightness) is int
            and 1 <= brightness <= DMX_MAX_VALUE
        ):
            return brightness, remembered
        return (None, remembered) if remembered else None
    try:
        level = float(state.state)
    except ValueError, TypeError:
        return (None, remembered) if remembered else None
    if not math.isfinite(level) or not 0 <= level <= 1:
        return (None, remembered) if remembered else None
    brightness = extra.get("brightness")
    if type(brightness) is not int or not 0 <= brightness <= DMX_MAX_VALUE:
        brightness = round(level * DMX_MAX_VALUE)
    return brightness, remembered


class UsbDmxRestoringDimmer(UsbDmxDimmerEntity, RestoreEntity):
    """Allow either active output role to initiate the single central restore."""

    @property
    @override
    def extra_restore_state_data(self) -> DimmerExtraData:
        """Save requested memory separately from current visible output."""
        return DimmerExtraData(self.dimmer.brightness, self.dimmer.last_on_brightness)

    @override
    async def async_added_to_hass(self) -> None:
        """Select the newest valid registry source, preferring Light at equal age."""
        await super().async_added_to_hass()
        if (
            self.entry.runtime_data.startup_behavior is not StartupBehavior.RESTORE
            or self.dimmer.restore_resolved
        ):
            return
        registry = er.async_get(self.hass)
        base = f"{self.entry.entry_id}_{self.fixture.fixture_id}"
        cache = async_get(self.hass).last_states
        candidates = []
        for domain, unique_id in (("light", base), ("number", f"{base}_level")):
            entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
            if (
                entity_id
                and (stored := cache.get(entity_id)) is not None
                and (values := _restore_values(stored, light=domain == "light"))
                is not None
            ):
                candidates.append(
                    (values[0] is not None, stored.last_seen, domain == "light", values)
                )
        if candidates:
            _, _, _, (brightness, remembered) = max(candidates)
        else:
            # Directly constructed entities also use the same resolver; real HA
            # entities always have their immutable registry identity above.
            state = await self.async_get_last_state()
            extra = await self.async_get_last_extra_data()
            values = None
            if state is not None:
                stored = StoredState(state, extra, state.last_updated)
                values = _restore_values(
                    stored, light=self.entity_id.startswith("light.")
                )
            brightness, remembered = values or (None, None)
        await self.dimmer.async_restore(brightness or None, remembered)
