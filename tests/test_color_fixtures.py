"""Regression coverage for multi-channel fixtures and color output."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from uuid import uuid4

import pytest

from custom_components.usb_dmx import UsbDmxRuntime
from custom_components.usb_dmx.controller import DmxController
from custom_components.usb_dmx.light import UsbDmxColorLight, async_setup_entry
from custom_components.usb_dmx.models import (
    FixtureConfig,
    FixtureType,
    FixtureValidationError,
    StartupBehavior,
    validate_fixtures,
)

from .fakes import FakeBackend

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)


def _fixture(fixture_type: FixtureType, address: int = 1) -> FixtureConfig:
    """Build one valid fixture without coupling tests to a fixed identity."""
    return FixtureConfig(
        fixture_id=str(uuid4()),
        fixture_type=fixture_type,
        name=f"{fixture_type.value} at {address}",
        address=address,
        minimum=0,
        maximum=255,
    )


@pytest.mark.parametrize(
    ("fixture_type", "start", "count"),
    [(FixtureType.DIMMER, 512, 1), (FixtureType.RAW, 512, 1),
     (FixtureType.RGB, 510, 3), (FixtureType.RGBW, 509, 4)],
)
def test_footprints_fit_at_last_possible_address(
    fixture_type: FixtureType, start: int, count: int
) -> None:
    """An entire fixture may end at the last slot of the universe."""
    fixture = _fixture(fixture_type, start)
    validate_fixtures([fixture])
    assert list(fixture.occupied_addresses) == list(range(start, start + count))


@pytest.mark.parametrize(
    ("fixture_type", "start"),
    [(FixtureType.RGB, 511), (FixtureType.RGBW, 510)],
)
def test_footprints_cannot_overflow_universe(
    fixture_type: FixtureType, start: int
) -> None:
    """Reject a fixture that starts legally but extends beyond channel 512."""
    with pytest.raises(FixtureValidationError, match="invalid_address"):
        validate_fixtures([_fixture(fixture_type, start)])


def test_multi_channel_overlap_is_detected_on_every_occupied_address() -> None:
    """Overlapping a fixture's interior channel is not allowed."""
    for address in (10, 11, 12):
        with pytest.raises(FixtureValidationError, match="duplicate_address"):
            validate_fixtures([
                _fixture(FixtureType.RGB, 10),
                _fixture(FixtureType.DIMMER, address),
            ])
    validate_fixtures([
        _fixture(FixtureType.RGB, 10),
        _fixture(FixtureType.RGBW, 13),
    ])


@pytest.mark.parametrize("fixture_type", [FixtureType.RGB, FixtureType.RGBW])
def test_color_fixtures_keep_backwards_compatible_storage(
    fixture_type: FixtureType
) -> None:
    """Persist color modes without introducing new required keys."""
    fixture = _fixture(fixture_type, 20)
    assert FixtureConfig.from_mapping(fixture.as_mapping()) == fixture


async def test_controller_color_update_is_one_frame_and_validated() -> None:
    """Multi-byte writes are atomic, and malformed input cannot change a frame."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    await controller.async_set_channels({20: 255, 21: 128, 22: 64})
    assert len(backend.frames) == 1
    assert backend.frames[-1][19:22] == bytes((255, 128, 64))
    with pytest.raises(ValueError):
        await controller.async_set_channels({20: 0, 513: 255})
    assert len(backend.frames) == 1
    await controller.async_set_channels({20: 0, 21: 0, 22: 0})
    assert len(backend.frames) == 2
    assert backend.frames[-1][19:22] == bytes(3)
    await controller.async_stop()


@pytest.mark.parametrize(
    ("fixture_type", "color", "expected"),
    [
        (FixtureType.RGB, (255, 128, 0), (128, 64, 0)),
        (FixtureType.RGBW, (0, 64, 128, 255), (0, 32, 64, 128)),
    ],
)
async def test_color_light_writes_atomic_brightness_scaled_channels(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    fixture_type: FixtureType,
    color: tuple[int, ...],
    expected: tuple[int, ...],
) -> None:
    """Color, brightness and off operate on the entire assigned footprint."""
    fixture = _fixture(fixture_type, 20)
    stored = make_fixture_subentry(
        fixture_id=fixture.fixture_id,
        fixture_type=fixture_type.value,
        name=fixture.name,
        address=fixture.address,
    )
    entry = make_usb_dmx_entry(stored)
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    entry.runtime_data = UsbDmxRuntime(controller, backend, StartupBehavior.ZERO)
    entity = UsbDmxColorLight(fixture, entry)
    key = "rgb_color" if fixture_type is FixtureType.RGB else "rgbw_color"
    await entity.async_turn_on(**{key: color, "brightness": 128})
    assert entity.is_on
    assert len(backend.frames) == 1
    assert backend.frames[-1][19:19 + fixture.channel_count] == bytes(expected)
    await entity.async_turn_off()
    assert not entity.is_on
    assert len(backend.frames) == 2
    assert backend.frames[-1][19:19 + fixture.channel_count] == bytes(
        fixture.channel_count
    )
    await controller.async_stop()


async def test_color_light_rejects_invalid_color_without_writing(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """An invalid component or wrong mode cannot partially write DMX."""
    fixture = _fixture(FixtureType.RGB, 1)
    entry = make_usb_dmx_entry(make_fixture_subentry(
        fixture_id=fixture.fixture_id, fixture_type="rgb", name=fixture.name,
    ))
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    entry.runtime_data = UsbDmxRuntime(controller, backend, StartupBehavior.ZERO)
    light = UsbDmxColorLight(fixture, entry)
    with pytest.raises(ValueError):
        await light.async_turn_on(rgb_color=(255, 256, 0))
    with pytest.raises(ValueError):
        await light.async_turn_on(rgbw_color=(255, 255, 255, 0))
    assert backend.frames == []
    await controller.async_stop()


async def test_light_platform_creates_color_fixture_under_its_subentry(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """One RGBW subentry becomes exactly one HA color light."""
    fixture = make_fixture_subentry(
        fixture_type="rgbw", name="Color bar", address=10
    )
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    entry.runtime_data = UsbDmxRuntime(controller, backend, StartupBehavior.ZERO)
    added: list[tuple[UsbDmxColorLight, str | None]] = []

    def add_entities(
        entities: list[UsbDmxColorLight],
        update_before_add: bool = False,  # noqa: FBT001, FBT002
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        assert not update_before_add
        added.append((entities[0], config_subentry_id))

    await async_setup_entry(hass, entry, add_entities)
    assert len(added) == 1
    assert isinstance(added[0][0], UsbDmxColorLight)
    assert added[0][1] == fixture.subentry_id
    await controller.async_stop()
