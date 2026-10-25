"""Regression tests for fixture personalities, palette mixing and LED bar pixels."""

from __future__ import annotations

from uuid import uuid4

import pytest

from custom_components.usb_dmx.color import ColorRuntime
from custom_components.usb_dmx.controller import DmxController
from custom_components.usb_dmx.models import (
    FixtureConfig,
    FixtureType,
    FixtureValidationError,
    PixelMode,
    validate_fixtures,
)

from .fakes import FakeBackend


def fixture(
    mode: FixtureType,
    address: int = 1,
    *,
    pixels: int = 1,
    pixel_mode: PixelMode = PixelMode.RGB,
) -> FixtureConfig:
    """Construct a distinct fixture for each tested channel personality."""
    return FixtureConfig(
        fixture_id=str(uuid4()),
        fixture_type=mode,
        name=f"{mode.value}:{address}",
        address=address,
        minimum=0,
        maximum=255,
        pixel_count=pixels,
        pixel_mode=pixel_mode,
    )


@pytest.mark.parametrize(
    ("kind", "start", "channels"),
    [
        (FixtureType.RGBWAUV, 507, 6),
        (FixtureType.DIMMER_RGB, 509, 4),
        (FixtureType.DIMMER_RGBW_STROBE, 507, 6),
    ],
)
def test_fixture_personalities_span_exact_channels(
    kind: FixtureType, start: int, channels: int
) -> None:
    """Check the final valid DMX start address for each supported personality."""
    setting = fixture(kind, start)
    validate_fixtures([setting])
    assert setting.channel_count == channels
    assert list(setting.occupied_addresses) == list(range(start, 513))
    with pytest.raises(FixtureValidationError, match="invalid_address"):
        validate_fixtures([fixture(kind, start + 1)])


@pytest.mark.parametrize(
    ("mode", "count", "start"),
    [(PixelMode.RGB, 4, 501), (PixelMode.RGBW, 3, 501)],
)
def test_led_bar_footprints_validate_channels(
    mode: PixelMode, count: int, start: int
) -> None:
    """Reserve every pixel channel, including the final DMX address."""
    bar = fixture(FixtureType.LED_BAR, start, pixels=count, pixel_mode=mode)
    validate_fixtures([bar])
    assert bar.channel_count == 12
    with pytest.raises(FixtureValidationError, match="duplicate_address"):
        validate_fixtures([bar, fixture(FixtureType.RAW, 506)])
    with pytest.raises(FixtureValidationError, match="invalid_address"):
        validate_fixtures(
            [fixture(FixtureType.LED_BAR, 502, pixels=count, pixel_mode=mode)]
        )


def test_led_bar_stored_settings_round_trip() -> None:
    """Persist variable pixel count and RGBW order, unlike legacy fixtures."""
    original = fixture(FixtureType.LED_BAR, 6, pixels=7, pixel_mode=PixelMode.RGBW)
    assert original.channel_count == 28
    assert FixtureConfig.from_mapping(original.as_mapping()) == original


@pytest.mark.parametrize(
    "data",
    [{"pixel_count": 0}, {"pixel_count": 171}, {"pixel_count": 1.3}],
)
def test_bad_pixel_count_is_rejected(data: dict[str, object]) -> None:
    """Reject malformed pixel counts at the storage boundary."""
    setting = fixture(FixtureType.LED_BAR)
    with pytest.raises(FixtureValidationError, match="invalid_pixel_count"):
        FixtureConfig.from_mapping(setting.as_mapping() | data)


async def test_rgbwauv_manual_mixing_and_palette() -> None:
    """Apply independent W/A/UV values and preserve free color mixing."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = ColorRuntime(controller, fixture(FixtureType.RGBWAUV, 20))
    await runtime.set_color(color=(255, 100, 0), brightness=128)
    await runtime.set_extra("white", 64)
    await runtime.set_extra("amber", 255)
    await runtime.set_extra("uv", 100)
    assert backend.frames[-1][19:25] == bytes((128, 50, 0, 32, 128, 50))
    assert runtime.palette == "custom"
    before = len(backend.frames)
    await runtime.apply_palette("uv")
    assert len(backend.frames) == before + 1
    assert backend.frames[-1][19:25] == bytes((0, 0, 0, 0, 0, 128))
    await runtime.set_color(color=(0, 100, 200))
    assert runtime.palette == "custom"
    await runtime.turn_off()
    assert backend.frames[-1][19:25] == bytes(6)
    await controller.async_stop()


async def test_master_dimmer_and_strobe_have_explicit_channel_offsets() -> None:
    """Brightness controls master only, while strobe stays off by default."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    dmx = ColorRuntime(controller, fixture(FixtureType.DIMMER_RGBW_STROBE, 1))
    await dmx.set_color(color=(255, 120, 40, 200), brightness=80)
    assert backend.frames[-1][:6] == bytes((80, 255, 120, 40, 200, 0))
    await dmx.set_extra("strobe", 180)
    assert backend.frames[-1][:6] == bytes((80, 255, 120, 40, 200, 180))
    await dmx.turn_off()
    assert backend.frames[-1][:6] == bytes(6)
    await controller.async_stop()


async def test_dimmer_rgb_master_does_not_scale_color_channels() -> None:
    """The physical RGB levels remain selected color while master changes."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    dmx = ColorRuntime(controller, fixture(FixtureType.DIMMER_RGB, 30))
    await dmx.set_color(color=(255, 70, 30), brightness=64)
    assert backend.frames[-1][29:33] == bytes((64, 255, 70, 30))
    await controller.async_stop()


async def test_pixel_bar_supports_independent_pixels_and_uniform_palette() -> None:
    """Group writes overwrite per-pixel colors; each change is one frame."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    bar = ColorRuntime(
        controller,
        fixture(FixtureType.LED_BAR, 100, pixels=3, pixel_mode=PixelMode.RGB),
    )
    await bar.set_color(color=(255, 0, 0), brightness=255)
    assert backend.frames[-1][99:108] == bytes((255, 0, 0) * 3)
    await bar.set_color(color=(0, 255, 0), pixel=1)
    assert backend.frames[-1][99:108] == bytes((255, 0, 0, 0, 255, 0, 255, 0, 0))
    await bar.apply_palette("blue")
    assert backend.frames[-1][99:108] == bytes((0, 0, 255) * 3)
    assert len(backend.frames) == 3
    await bar.turn_off(pixel=0)
    assert backend.frames[-1][99:108] == bytes((0, 0, 0, 0, 0, 255, 0, 0, 255))
    await controller.async_stop()
