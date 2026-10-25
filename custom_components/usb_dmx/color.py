"""Shared color mixer for RGB, RGBW, RGBWAUV and configurable pixel bars."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from .const import DMX_MAX_VALUE
from .controller import DmxController
from .models import FixtureConfig, FixtureType, PixelMode

PALETTE: dict[str, tuple[int, int, int]] = {
    "red": (255, 0, 0),
    "orange": (255, 80, 0),
    "yellow": (255, 255, 0),
    "green": (0, 255, 0),
    "cyan": (0, 255, 255),
    "blue": (0, 0, 255),
    "purple": (128, 0, 255),
    "magenta": (255, 0, 255),
    "pink": (255, 50, 110),
    "warm_white": (255, 170, 90),
    "cool_white": (210, 230, 255),
}


class ColorRuntime:
    """Own a fixture's mixer and publish one atomic DMX update per action."""

    def __init__(self, controller: DmxController, fixture: FixtureConfig) -> None:
        """Initialize state without issuing any output during setup."""
        self.controller = controller
        self.fixture = fixture
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []
        self._is_rgbw = fixture.fixture_type in (
            FixtureType.RGBW, FixtureType.DIMMER_RGBW_STROBE
        ) or (
            fixture.fixture_type is FixtureType.LED_BAR
            and fixture.pixel_mode is PixelMode.RGBW
        )
        self._color = (255, 255, 255, 0) if self._is_rgbw else (255, 255, 255)
        self._brightness = 255
        self._is_on = False
        self._extras: dict[str, int] = {"white": 0, "amber": 0, "uv": 0, "strobe": 0}
        self._palette = "custom"
        self._pixels: list[tuple[tuple[int, ...], int, bool]] = [
            (self._color, 255, False)
            for _ in range(
                fixture.pixel_count
                if fixture.fixture_type is FixtureType.LED_BAR
                else 0
            )
        ]

    @property
    def is_rgbw(self) -> bool:
        """Distinguish the native RGBW picker from an RGB picker."""
        return self._is_rgbw

    @property
    def palette_options(self) -> list[str]:
        """Expose supported colors and physical-channel-only presets."""
        options = ["custom", *PALETTE]
        if self.fixture.fixture_type is FixtureType.RGBWAUV:
            options.extend(("amber", "uv"))
        return options

    @property
    def palette(self) -> str:
        """Return the selected palette entry or custom when manually edited."""
        return self._palette

    def extra_roles(self) -> tuple[str, ...]:
        """List independently adjustable non-RGB values in physical order."""
        match self.fixture.fixture_type:
            case FixtureType.RGBWAUV:
                return ("white", "amber", "uv")
            case FixtureType.RGBW | FixtureType.DIMMER_RGBW_STROBE:
                return ("white", "strobe") if (
                    self.fixture.fixture_type is FixtureType.DIMMER_RGBW_STROBE
                ) else ("white",)
            case _:
                return ()

    def extra_value(self, name: str) -> int:
        """Read a physical-channel slider in the active configuration."""
        if name not in self.extra_roles():
            msg = "Channel is not present in this fixture"
            raise ValueError(msg)
        if name == "white" and self._is_rgbw:
            return self._color[3]
        return self._extras[name]

    def add_listener(self, listener: Callable[[], None]) -> Callable[[], None]:
        """Subscribe multiple HA entities to shared mixer changes."""
        self._listeners.append(listener)

        def unsubscribe() -> None:
            if listener in self._listeners:
                self._listeners.remove(listener)

        return unsubscribe

    def _publish(self) -> None:
        for listener in tuple(self._listeners):
            listener()

    def get_state(self, pixel: int | None = None) -> tuple[tuple[int, ...], int, bool]:
        """Return the requested color, brightness and enabled state."""
        if pixel is not None:
            return self._pixels[pixel]
        if self._pixels:
            return (
                self._color,
                self._brightness,
                any(state[2] for state in self._pixels),
            )
        return self._color, self._brightness, self._is_on

    @staticmethod
    def _validate_byte(value: object) -> int:
        if type(value) is not int or not 0 <= value <= DMX_MAX_VALUE:
            msg = "DMX channel value must be an integer from 0 to 255"
            raise ValueError(msg)
        return value

    def _validate_color(self, value: object) -> tuple[int, ...]:
        if not isinstance(value, (list, tuple)) or len(value) != len(self._color):
            msg = "Color channels do not match the fixture's mode"
            raise ValueError(msg)
        return tuple(self._validate_byte(component) for component in value)

    def _render(self) -> dict[int, int]:
        """Map all currently requested fixture channels into one DMX frame."""
        fixture = self.fixture
        kind = fixture.fixture_type
        if kind is FixtureType.LED_BAR:
            values: dict[int, int] = {}
            stride = len(self._color)
            for pixel, (color, brightness, enabled) in enumerate(self._pixels):
                for offset, component in enumerate(color):
                    values[fixture.address + pixel * stride + offset] = (
                        round(component * brightness / 255) if enabled else 0
                    )
            return values

        enabled = self._is_on and self._brightness > 0
        color = self._color
        if kind in (FixtureType.DIMMER_RGB, FixtureType.DIMMER_RGBW_STROBE):
            channels = [self._brightness if enabled else 0]
            channels.extend(color if enabled else (0,) * len(color))
            if kind is FixtureType.DIMMER_RGBW_STROBE:
                channels.append(self._extras["strobe"] if enabled else 0)
        else:
            channels = [
                round(value * self._brightness / 255) if enabled else 0
                for value in color
            ]
            if kind is FixtureType.RGBWAUV:
                channels.extend(
                    round(self._extras[role] * self._brightness / 255)
                    if enabled else 0
                    for role in ("white", "amber", "uv")
                )
        return dict(zip(fixture.occupied_addresses, channels, strict=True))

    async def set_color(
        self,
        *,
        color: object | None = None,
        brightness: int | None = None,
        pixel: int | None = None,
    ) -> None:
        """Apply a picker change, optionally to one independent bar pixel."""
        if pixel is not None and not 0 <= pixel < len(self._pixels):
            msg = "Invalid LED bar pixel"
            raise ValueError(msg)
        previous_color, previous_brightness, _ = self.get_state(pixel)
        new_color = previous_color if color is None else self._validate_color(color)
        new_brightness = (
            previous_brightness
            if brightness is None else self._validate_byte(brightness)
        )
        async with self._lock:
            if pixel is None:
                self._color = new_color
                self._brightness = new_brightness
                self._is_on = new_brightness > 0
                if self._pixels:
                    self._pixels = [
                        (new_color, new_brightness, new_brightness > 0)
                        for _ in self._pixels
                    ]
            else:
                self._pixels[pixel] = (
                    new_color, new_brightness, new_brightness > 0
                )
            self._palette = "custom"
            await self.controller.async_set_channels(self._render())
            self._publish()

    async def turn_off(self, *, pixel: int | None = None) -> None:
        """Black out only the selected pixels, preserving chosen colors."""
        if pixel is not None and not 0 <= pixel < len(self._pixels):
            msg = "Invalid LED bar pixel"
            raise ValueError(msg)
        async with self._lock:
            if pixel is None:
                self._is_on = False
                self._pixels = [
                    (color, level, False) for color, level, _ in self._pixels
                ]
            else:
                color, level, _ = self._pixels[pixel]
                self._pixels[pixel] = (color, level, False)
            await self.controller.async_set_channels(self._render())
            self._publish()

    async def set_extra(self, name: str, value: int) -> None:
        """Set one W/A/UV/Strobe slider and recalculate the fixture atomically."""
        value = self._validate_byte(value)
        if name not in self.extra_roles():
            msg = "Channel is not present in this fixture"
            raise ValueError(msg)
        async with self._lock:
            if name == "white" and self._is_rgbw:
                self._color = (*self._color[:3], value)
            else:
                self._extras[name] = value
            self._palette = "custom"
            await self.controller.async_set_channels(self._render())
            self._publish()

    async def apply_palette(self, option: str) -> None:
        """Use a reproducible preset while retaining separate W/A/UV mixing."""
        if option not in self.palette_options:
            msg = "Unknown fixture palette preset"
            raise ValueError(msg)
        if option == "custom":
            return
        async with self._lock:
            rgb = PALETTE.get(option, (0, 0, 0))
            if self._is_rgbw:
                self._color = (*rgb, 255 if option.endswith("_white") else 0)
            else:
                self._color = rgb
            for role in ("white", "amber", "uv"):
                self._extras[role] = 255 if option == role else 0
            if option.endswith("_white") and not self._is_rgbw:
                self._extras["white"] = 255 if "white" in self.extra_roles() else 0
            self._is_on = True
            self._brightness = max(1, self._brightness)
            if self._pixels:
                self._pixels = [
                    (self._color, self._brightness, True) for _ in self._pixels
                ]
            self._palette = option
            await self.controller.async_set_channels(self._render())
            self._publish()

    async def restore(
        self, color: object, brightness: int, extras: dict[str, object]
    ) -> None:
        """Restore validated HA state in one frame without remembering a strobe."""
        new_color = self._validate_color(color)
        new_brightness = self._validate_byte(brightness)
        new_extras = {}
        for role in self.extra_roles():
            if role != "strobe" and role in extras:
                new_extras[role] = self._validate_byte(extras[role])
        async with self._lock:
            self._color = new_color
            self._brightness = new_brightness
            self._is_on = new_brightness > 0
            if self._is_rgbw and "white" in new_extras:
                self._color = (*self._color[:3], new_extras["white"])
            self._extras.update(new_extras)
            if self._pixels:
                self._pixels = [
                    (self._color, new_brightness, new_brightness > 0)
                    for _ in self._pixels
                ]
            await self.controller.async_set_channels(self._render())
            self._publish()
