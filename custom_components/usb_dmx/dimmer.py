"""Shared logical dimmer state driven by the controller's transition timebase."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import Callable

from homeassistant.config_entries import UnknownSubEntry

from .const import DMX_MAX_VALUE
from .controller import DmxController
from .models import FadeSource, FixtureConfig
from .settings import FadeSettings

_LOGGER = logging.getLogger(__name__)
_MAX_TRANSITION_SECONDS = 6553


class DimmerRuntime:
    """Serialize all fixture commands and own its logical brightness and restore."""

    def __init__(
        self,
        controller: DmxController,
        fixture: FixtureConfig,
        settings: FadeSettings | None = None,
    ) -> None:
        """Initialize without issuing a brightness or restore write."""
        self.controller = controller
        self.fixture = fixture
        self.settings = settings
        self.logical_brightness = 0.0
        self.last_on_brightness: int | None = None
        self.fade_time = fixture.fade_time
        self.fade_source = fixture.fade_source
        self._command_removal_lock = asyncio.Lock()
        self._removing = False
        self._removed = False
        self._restored = False
        self._generation = 0
        self._listeners: list[Callable[[], None]] = []
        self._unsubscribe_settings = (
            settings.add_listener(self._settings_changed) if settings else None
        )
        if settings:
            self.fade_time, self.fade_source = settings.fixture_settings(
                fixture.fixture_id
            )

    @property
    def brightness(self) -> int | None:
        """Publish at least one while continuous logical output is still positive."""
        return max(1, round(self.logical_brightness)) if self.is_on else None

    @property
    def is_on(self) -> bool:
        """Remain on until the exact logical zero endpoint."""
        return self.logical_brightness > 0

    @property
    def level(self) -> float:
        """Match the normalized fader's rounded light-brightness semantics."""
        return round((self.brightness or 0) / DMX_MAX_VALUE, 2)

    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to shared state and settings changes."""
        self._listeners.append(callback)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._listeners.remove(callback)

        return unsubscribe

    def _publish(self) -> None:
        for listener in tuple(self._listeners):
            try:
                listener()
            except Exception:
                _LOGGER.exception("USB DMX dimmer state listener failed")

    def _settings_changed(self) -> None:
        if self._removing or self.settings is None:
            return
        try:
            self.fade_time, self.fade_source = self.settings.fixture_settings(
                self.fixture.fixture_id
            )
        except UnknownSubEntry:
            return
        self._publish()

    def _duration(self, transition: object | None) -> float:
        if transition is not None:
            return self._validated_number(
                transition, _MAX_TRANSITION_SECONDS, "transition"
            )
        if self.fade_source is FadeSource.INDIVIDUAL:
            return self.fade_time
        return self.settings.global_fade_time if self.settings else 0.0

    async def async_turn_on(
        self, brightness: object | None = None, *, transition: object | None = None
    ) -> None:
        """Admit an explicit or remembered target with the current configured time."""
        if brightness is not None:
            self._validate_brightness(brightness)
        if transition is not None:
            self._validated_number(transition, _MAX_TRANSITION_SECONDS, "transition")
        async with self._command_removal_lock:
            if self._removing:
                return
            target = (
                brightness
                if brightness is not None
                else self.last_on_brightness or DMX_MAX_VALUE
            )
            await self._async_apply(target, self._duration(transition))

    async def async_turn_off(self, *, transition: object | None = None) -> None:
        """Fade to exact zero while keeping the last requested positive target."""
        await self.async_turn_on(0, transition=transition)

    async def async_set_level(self, value: object) -> None:
        """Convert an unquantized normalized number into the rounded HA target."""
        level = self._validated_number(value, 1, "level")
        await self.async_turn_on(round(level * DMX_MAX_VALUE))

    async def _async_apply(
        self, target: int, duration: float, *, remember_target: bool = True
    ) -> None:
        self._generation += 1
        generation = self._generation
        start = self.logical_brightness
        if target > 0 and remember_target:
            self.last_on_brightness = target

        def progress(fraction: float) -> None:
            if generation != self._generation or self._removing:
                return
            self.logical_brightness = start + (target - start) * fraction
            self._publish()

        await self.controller.async_transition_channel(
            self.fixture.address,
            self._brightness_to_dmx(target),
            duration,
            progress_callback=progress,
        )

    def _brightness_to_dmx(self, brightness: int) -> int:
        if brightness == 0:
            return 0
        return round(
            self.fixture.minimum
            + (brightness - 1)
            * (self.fixture.maximum - self.fixture.minimum)
            / (DMX_MAX_VALUE - 1)
        )

    async def async_restore(
        self, brightness: object | None, last_on_brightness: object | None = None
    ) -> None:
        """Apply the single selected restore state immediately and at most once."""
        async with self._command_removal_lock:
            if self._removing or self._restored:
                return
            self._restored = True
            remembered = self._restored_brightness(last_on_brightness)
            if remembered:
                self.last_on_brightness = remembered
            if (restored := self._restored_brightness(brightness)) is not None:
                await self._async_apply(restored, 0, remember_target=not remembered)

    async def async_set_fade_time(self, value: object) -> None:
        """Coordinate fixture settings with commands and terminal removal."""
        async with self._command_removal_lock:
            if not self._removing and self.settings is not None:
                await self.settings.async_set_fixture_fade_time(
                    self.fixture.fixture_id, value
                )

    async def async_set_fade_source(self, value: object) -> None:
        """Change only future command timing under the shared fixture lock."""
        async with self._command_removal_lock:
            if not self._removing and self.settings is not None:
                await self.settings.async_set_fixture_fade_source(
                    self.fixture.fixture_id, value
                )

    async def async_remove(self, *, blackout: bool) -> None:
        """Mark terminal before waiting, then cancel and optionally clear one slot."""
        self._removing = True
        self._generation += 1
        async with self._command_removal_lock:
            if self._removed:
                return
            await self.controller.async_cancel_transition(self.fixture.address)
            if blackout:
                await self.controller.async_set_channel(self.fixture.address, 0)
                self.logical_brightness = 0.0
            if self._unsubscribe_settings is not None:
                self._unsubscribe_settings()
                self._unsubscribe_settings = None
            self._listeners.clear()
            self._removed = True

    @staticmethod
    def _validate_brightness(value: object) -> None:
        message = "brightness must be an integer between 0 and 255"
        if type(value) is not int:
            raise TypeError(message)
        if not 0 <= value <= DMX_MAX_VALUE:
            raise ValueError(message)

    @classmethod
    def _restored_brightness(cls, value: object) -> int | None:
        try:
            cls._validate_brightness(value)
        except TypeError, ValueError:
            return None
        return value

    @staticmethod
    def _validated_number(value: object, maximum: float, name: str) -> float:
        message = f"{name} must be a finite number between 0 and {maximum}"
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise TypeError(message)
        if not math.isfinite(value) or not 0 <= value <= maximum:
            raise ValueError(message)
        return float(value)
