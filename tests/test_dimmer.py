"""Tests for shared logical dimmer commands, progress, restore and removal."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING

import pytest

from custom_components.usb_dmx.controller import DmxController
from custom_components.usb_dmx.dimmer import DimmerRuntime
from custom_components.usb_dmx.models import FadeSource, FixtureConfig, FixtureType
from custom_components.usb_dmx.settings import FadeSettings

from .fakes import FakeBackend

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


@pytest.fixture
def runtime_class() -> type:
    """Expose the shared runtime constructor for direct command tests."""
    return DimmerRuntime


def _fixture(*, minimum: int = 0, maximum: int = 255) -> FixtureConfig:
    """Create a dimmer with controllable physical range."""
    return FixtureConfig("fixture", FixtureType.DIMMER, "Dimmer", 1, minimum, maximum)


@pytest.mark.parametrize(
    ("value", "target"),
    [(0, 0), (0.3, 76), (0.5, 128), (1, 255), (0.306, 78), (0.001, 0)],
)
async def test_level_rounding_and_remembered_targets(
    runtime_class: type, value: float, target: int
) -> None:
    """Normalized inputs use Python rounding without prior grid quantization."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(controller, _fixture())
    await runtime.async_set_level(value)
    assert runtime.logical_brightness == target
    assert runtime.brightness == (target or None)
    assert runtime.is_on == bool(target)
    assert runtime.level == round(target / 255, 2)
    expected = round((target - 1) * 255 / 254) if target else 0
    assert controller.current_frame[0] == expected
    await runtime.async_turn_off()
    assert runtime.last_on_brightness == (target or None)
    await runtime.async_turn_on()
    assert runtime.brightness == (target or 255)
    await controller.async_stop()


@pytest.mark.parametrize(("minimum", "maximum"), [(0, 0), (70, 70), (30, 180)])
async def test_live_logical_progress_is_independent_of_physical_range(
    runtime_class: type, minimum: int, maximum: int
) -> None:
    """Logical progress continues across constant bytes and remains on below half."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(controller, _fixture(minimum=minimum, maximum=maximum))
    await runtime.async_turn_on(1)
    observations = []
    finished = asyncio.Event()

    def changed() -> None:
        observations.append(
            (runtime.logical_brightness, runtime.brightness, runtime.is_on)
        )
        if not runtime.is_on:
            finished.set()

    runtime.add_listener(changed)
    await runtime.async_turn_off(transition=0.1)
    assert runtime.is_on
    await asyncio.wait_for(finished.wait(), 1)
    assert any(
        0 < logical < 0.5 and brightness == 1 and on
        for logical, brightness, on in observations
    )
    assert observations[-1] == (0, None, False)
    assert runtime.last_on_brightness == 1
    assert controller.current_frame[0] == 0
    await controller.async_stop()


async def test_restore_once_immediate_and_preserves_positive_target(
    runtime_class: type,
) -> None:
    """Central restore writes once without a configured fade and tolerates bad data."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    fixture = _fixture()
    fixture = FixtureConfig(
        fixture.fixture_id,
        fixture.fixture_type,
        fixture.name,
        1,
        0,
        255,
        2,
        FadeSource.INDIVIDUAL,
    )
    runtime = runtime_class(controller, fixture)
    await runtime.async_restore(76, 128)
    await runtime.async_restore(255)
    assert runtime.brightness == 76
    assert runtime.last_on_brightness == 128
    assert len(backend.frames) == 1
    assert not controller._transition_tasks
    await runtime.async_turn_off(transition=0)
    await runtime.async_turn_on(transition=0)
    assert runtime.brightness == 128
    await controller.async_stop()


async def test_deletion_wins_over_queued_commands_and_is_idempotent(
    runtime_class: type,
) -> None:
    """Removal marks terminal before waiting so old FIFO waiters cannot revive it."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    await controller.async_set_channel(2, 49)
    runtime = runtime_class(controller, _fixture())
    backend.send_gate = asyncio.Event()
    backend.send_started.clear()
    admitted = asyncio.create_task(runtime.async_turn_on(90))
    await backend.send_started.wait()
    queued = asyncio.create_task(runtime.async_set_level(1))
    await asyncio.sleep(0)
    removing = asyncio.create_task(runtime.async_remove(blackout=True))
    await asyncio.sleep(0)
    backend.send_gate.set()
    await asyncio.gather(admitted, queued, removing)
    await runtime.async_turn_on(255)
    count = len(backend.frames)
    await runtime.async_remove(blackout=True)
    assert len(backend.frames) == count
    assert controller.current_frame[:2] == bytes((0, 49))
    assert [frame[0] for frame in backend.frames] == [0, 89, 0]
    assert not runtime.is_on
    await controller.async_stop()


async def test_settings_priority_future_commands_and_no_output(
    runtime_class: type,
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Settings change only future commands and explicit zero overrides both sources."""
    subentry = make_fixture_subentry()
    entry = make_usb_dmx_entry(subentry)
    entry.add_to_hass(hass)
    settings = FadeSettings(hass, entry)
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(
        controller, FixtureConfig.from_mapping(subentry.data), settings
    )
    await settings.async_set_global_fade_time(0.2)
    await runtime.async_set_fade_time(0)
    assert backend.frames == []
    await runtime.async_turn_on(255)
    assert not runtime.is_on
    await settings.async_set_global_fade_time(0)
    await asyncio.sleep(0.04)
    assert 0 < runtime.logical_brightness < 255
    await runtime.async_turn_on(100, transition=0)
    assert runtime.brightness == 100
    await settings.async_set_global_fade_time(2)
    await runtime.async_set_fade_source("individual")
    await runtime.async_turn_off()
    assert not runtime.is_on
    assert runtime.fade_time == 0
    assert runtime.fade_source is FadeSource.INDIVIDUAL
    await runtime.async_remove(blackout=False)
    count = len(backend.frames)
    await runtime.async_set_fade_time(1)
    assert runtime.fade_time == 0
    assert len(backend.frames) == count
    await controller.async_stop()


@pytest.mark.parametrize("value", [True, "0.5", float("nan"), float("inf"), -0.1, 1.1])
async def test_bad_levels_do_not_change_state(
    runtime_class: type, value: object
) -> None:
    """Invalid direct-call normalized inputs are rejected before output."""
    backend = FakeBackend()
    controller = DmxController(backend)
    runtime = runtime_class(controller, _fixture())
    with pytest.raises((TypeError, ValueError)):
        await runtime.async_set_level(value)
    assert runtime.logical_brightness == 0
    assert backend.send_attempts == 0


async def test_offline_progress_and_reconnect_replays_current_frame(
    runtime_class: type,
) -> None:
    """Logical fades finish offline and reconnect sends the current full universe."""
    backend = FakeBackend()
    controller = DmxController(backend, reconnect_delays=(0,))
    await controller.async_start()
    await controller.async_set_channel(2, 46)
    runtime = runtime_class(controller, _fixture())
    backend.reconnect_gate = asyncio.Event()
    backend.send_failures = 1
    await runtime.async_turn_on(255, transition=0.1)
    await backend.reconnect_started.wait()
    await asyncio.sleep(0.12)
    assert runtime.brightness == 255
    assert not controller.available
    assert controller.current_frame[:2] == bytes((255, 46))
    backend.reconnect_gate.set()
    async with asyncio.timeout(1):
        while not controller.available:  # noqa: ASYNC110
            await asyncio.sleep(0)
    assert backend.frames[-1][:2] == bytes((255, 46))
    assert backend.reconnect_calls == 1
    await controller.async_stop()


async def test_alternating_commands_cancel_stale_finals(runtime_class: type) -> None:
    """Shared commands replace fades from current progress without stale finals."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(controller, _fixture())
    await runtime.async_turn_on(255, transition=0.1)
    await asyncio.sleep(0.035)
    current = runtime.logical_brightness
    await runtime.async_turn_off(transition=0.2)
    assert runtime.logical_brightness == current
    await asyncio.sleep(0.035)
    await runtime.async_set_level(0.3)
    await asyncio.sleep(0.22)
    assert runtime.brightness == 76
    assert runtime.last_on_brightness == 76
    assert not controller._transition_tasks
    assert backend.frames[-1][0] == 75
    await controller.async_stop()


@pytest.mark.parametrize(
    ("brightness", "remembered", "target"),
    [
        (None, 76, None),
        (True, 76, None),
        (256, -1, None),
        (0, 128, None),
        ("76", True, None),
    ],
)
async def test_restore_invalid_data_and_off_memory(
    runtime_class: type, brightness: object, remembered: object, target: int | None
) -> None:
    """Malformed restore data is ignored and off memory survives a central restore."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(controller, _fixture())
    await runtime.async_restore(brightness, remembered)
    assert runtime.brightness is target
    assert runtime.last_on_brightness == (
        remembered if type(remembered) is int and 0 < remembered <= 255 else None
    )
    assert len(backend.frames) == (
        1 if type(brightness) is int and brightness == 0 else 0
    )
    await runtime.async_restore(255)
    assert runtime.brightness is target
    await controller.async_stop()


@pytest.mark.parametrize("value", [True, "0.1", float("nan"), float("inf"), -1, 6554])
async def test_bad_transitions_leave_state_unchanged(
    runtime_class: type, value: object
) -> None:
    """Explicit transitions retain the existing direct-call validation contract."""
    backend = FakeBackend()
    controller = DmxController(backend)
    runtime = runtime_class(controller, _fixture())
    with pytest.raises((TypeError, ValueError)):
        await runtime.async_turn_on(255, transition=value)
    assert runtime.last_on_brightness is None
    assert runtime.logical_brightness == 0


async def test_remove_without_blackout_cancels_and_unsubscribes(
    runtime_class: type,
) -> None:
    """Normal unload retains the last byte and removes all state subscriptions."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(controller, _fixture())
    observed = []
    unsubscribe = runtime.add_listener(lambda: observed.append(runtime.brightness))
    await runtime.async_turn_on(255, transition=0.2)
    await asyncio.sleep(0.04)
    await runtime.async_remove(blackout=False)
    current = controller.current_frame
    count = len(backend.frames)
    notifications = len(observed)
    unsubscribe()
    await asyncio.sleep(0.2)
    assert controller.current_frame == current
    assert len(backend.frames) == count
    assert len(observed) == notifications
    assert not controller._transition_tasks
    assert not runtime._listeners
    await controller.async_stop()


async def test_restore_keeps_requested_target_separate_from_inflight_current(
    runtime_class: type,
) -> None:
    """A saved intermediate brightness cannot replace a valid extra target memory."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(controller, _fixture())
    await runtime.async_restore(128, 200)
    assert runtime.brightness == 128
    assert runtime.last_on_brightness == 200
    assert len(backend.frames) == 1
    await runtime.async_turn_off(transition=0)
    await runtime.async_turn_on(transition=0)
    assert runtime.brightness == 200
    await controller.async_stop()


@pytest.mark.parametrize("remembered", [None, 0, True, "200", 256])
async def test_restore_legacy_positive_current_supplies_memory(
    runtime_class: type, remembered: object
) -> None:
    """Absent or invalid extra data falls back to the saved positive light state."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(controller, _fixture())
    await runtime.async_restore(128, remembered)
    assert runtime.brightness == runtime.last_on_brightness == 128
    await controller.async_stop()


async def test_deletion_rejects_queued_fixture_setting_writes(
    runtime_class: type,
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Terminal removal rejects earlier setting waiters before they touch storage."""
    subentry = make_fixture_subentry()
    entry = make_usb_dmx_entry(subentry)
    entry.add_to_hass(hass)
    settings = FadeSettings(hass, entry)
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    runtime = runtime_class(
        controller, FixtureConfig.from_mapping(subentry.data), settings
    )
    backend.send_gate = asyncio.Event()
    admitted = asyncio.create_task(runtime.async_turn_on(76))
    await backend.send_started.wait()
    time_write = asyncio.create_task(runtime.async_set_fade_time(2))
    source_write = asyncio.create_task(runtime.async_set_fade_source("individual"))
    await asyncio.sleep(0)
    remove = asyncio.create_task(runtime.async_remove(blackout=True))
    await asyncio.sleep(0)
    backend.send_gate.set()
    await asyncio.gather(admitted, time_write, source_write, remove)
    assert settings.fixture_settings(subentry.unique_id) == (0, FadeSource.GLOBAL)
    assert controller.current_frame[0] == 0
    assert not settings._listeners
    await controller.async_stop()
