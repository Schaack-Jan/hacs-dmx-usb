"""Tests for the stateful DMX universe controller."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from types import TracebackType
from typing import Any, Self

import pytest

from custom_components.usb_dmx.controller import DmxController

from .fakes import FakeBackend


class _FirstAcquireGate:
    """Block the first state-lock acquisition until a test releases it."""

    def __init__(self) -> None:
        """Initialize the gate and its underlying mutual-exclusion lock."""
        self.first_acquire_entered = asyncio.Event()
        self.release_first_acquire = asyncio.Event()
        self._lock = asyncio.Lock()
        self._acquisition_count = 0

    async def __aenter__(self) -> Self:
        """Gate the first caller, then acquire the underlying lock."""
        self._acquisition_count += 1
        if self._acquisition_count == 1:
            self.first_acquire_entered.set()
            await self.release_first_acquire.wait()
        await self._lock.acquire()
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        """Release the underlying lock."""
        self._lock.release()


class _RecordingTaskController(DmxController):
    """Record controller-created tasks so shutdown ownership is observable."""

    def __init__(self, backend: FakeBackend) -> None:
        """Initialize the controller and task recorder."""
        super().__init__(backend, reconnect_delays=(60,))
        self.created_tasks: list[asyncio.Task[None]] = []

    def _create_task(
        self, coroutine: Coroutine[Any, Any, None], *, name: str
    ) -> asyncio.Task[None]:
        """Create and retain a task reference for lifecycle assertions."""
        task = super()._create_task(coroutine, name=name)
        self.created_tasks.append(task)
        return task

    @property
    def transition_tasks(self) -> list[asyncio.Task[None]]:
        """Return every transition task created so far."""
        return [
            task
            for task in self.created_tasks
            if task.get_name().startswith("usb_dmx_transition_")
        ]


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Yield until a synchronous condition becomes true."""
    async with asyncio.timeout(1):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0)


def test_channel_boundaries_map_to_first_and_last_frame_bytes() -> None:
    """One-based DMX addresses mutate the corresponding zero-based slots."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = DmxController(backend)
        await controller.async_start()

        await controller.async_set_channel(1, 17)
        await controller.async_set_channel(512, 231)

        assert controller.current_frame[0] == 17
        assert controller.current_frame[-1] == 231
        assert backend.frames[-1] == bytes((17,)) + bytes(510) + bytes((231,))
        await controller.async_stop()

    asyncio.run(exercise())


@pytest.mark.parametrize(
    ("address", "value"),
    [(0, 0), (513, 0), (1, -1), (1, 256), (True, 0), (1, False)],
)
def test_set_channel_rejects_values_instead_of_clamping(
    address: int, value: int
) -> None:
    """Invalid address/value inputs raise without mutating the frame."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = DmxController(backend)
        await controller.async_start()

        with pytest.raises(ValueError, match="DMX"):
            await controller.async_set_channel(address, value)

        assert controller.current_frame == bytes(512)
        assert backend.frames == []
        await controller.async_stop()

    asyncio.run(exercise())


def test_concurrent_mutations_send_consistent_complete_frames() -> None:
    """A second mutation waits until the first complete frame is sent."""

    async def exercise() -> None:
        backend = FakeBackend()
        backend.send_gate = asyncio.Event()
        controller = DmxController(backend)
        await controller.async_start()

        first = asyncio.create_task(controller.async_set_channel(1, 10))
        await backend.send_started.wait()
        second = asyncio.create_task(controller.async_set_channel(2, 20))
        await asyncio.sleep(0)

        assert backend.send_attempts == 1
        backend.send_gate.set()
        await asyncio.gather(first, second)

        assert backend.frames == [
            bytes((10,)) + bytes(511),
            bytes((10, 20)) + bytes(510),
        ]
        await controller.async_stop()

    asyncio.run(exercise())


def test_new_transition_replaces_previous_transition_from_current_value() -> None:
    """A replacement transition prevents the prior target from winning later."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = DmxController(backend)
        await controller.async_start()

        await controller.async_transition_channel(1, 255, 0.2)
        await asyncio.sleep(0.08)
        replacement_start = controller.current_frame[0]
        assert 0 < replacement_start < 255

        await controller.async_transition_channel(1, 0, 0.05)
        await asyncio.sleep(0.25)

        assert controller.current_frame[0] == 0
        assert backend.frames[-1][0] == 0
        assert all(frame[0] != 255 for frame in backend.frames)
        await controller.async_stop()

    asyncio.run(exercise())


def test_concurrent_transition_replacements_remain_owned_until_stop() -> None:
    """Concurrent same-address admissions leave no untracked task at stop."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = _RecordingTaskController(backend)
        await controller.async_start()
        gate = _FirstAcquireGate()
        controller._lock = gate  # type: ignore[assignment]

        first = asyncio.create_task(controller.async_transition_channel(1, 200, 60))
        await gate.first_acquire_entered.wait()
        second = asyncio.create_task(controller.async_transition_channel(1, 100, 60))
        await asyncio.sleep(0)
        gate.release_first_acquire.set()
        await asyncio.gather(first, second)

        await controller.async_stop()

        assert len(controller.transition_tasks) == 2
        assert all(task.done() for task in controller.transition_tasks)

    asyncio.run(exercise())


def test_set_channel_serializes_against_transition_admission() -> None:
    """A later direct set cancels a concurrently admitted transition."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = _RecordingTaskController(backend)
        await controller.async_start()
        gate = _FirstAcquireGate()
        controller._lock = gate  # type: ignore[assignment]

        transition = asyncio.create_task(
            controller.async_transition_channel(1, 200, 60)
        )
        await gate.first_acquire_entered.wait()
        direct_set = asyncio.create_task(controller.async_set_channel(1, 77))
        await asyncio.sleep(0)
        gate.release_first_acquire.set()
        await asyncio.gather(transition, direct_set)

        assert controller.current_frame[0] == 77
        assert len(controller.transition_tasks) == 1
        assert controller.transition_tasks[0].done()
        await controller.async_stop()

    asyncio.run(exercise())


def test_transition_after_stop_is_not_admitted() -> None:
    """Stopping prevents new background transition task ownership."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = _RecordingTaskController(backend)
        await controller.async_start()
        await controller.async_stop()

        await controller.async_transition_channel(1, 200, 60)

        assert controller.transition_tasks == []
        assert controller.current_frame == bytes(512)

    asyncio.run(exercise())


def test_send_failure_reconnects_once_and_replays_current_frame(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """A failed write marks unavailable then reconnects and restores state."""

    async def exercise() -> None:
        backend = FakeBackend()
        backend.send_failures = 1
        controller = DmxController(backend, reconnect_delays=(0,))
        availability: list[bool] = []
        controller.add_availability_listener(availability.append)
        caplog.set_level(logging.INFO, logger="custom_components.usb_dmx.controller")
        await controller.async_start()

        await controller.async_set_channel(512, 99)
        await _wait_until(lambda: controller.available)

        assert availability == [True, False, True]
        assert backend.reconnect_calls == 1
        assert backend.disconnect_calls == 1
        assert backend.frames == [bytes(511) + bytes((99,))]
        assert controller.reconnect_count == 1
        assert controller.last_successful_frame_timestamp is not None
        await controller.async_stop()

        state_records = [
            (record.levelname, record.getMessage())
            for record in caplog.records
            if record.name == "custom_components.usb_dmx.controller"
            and "availability" in record.getMessage()
        ]
        assert state_records == [
            ("INFO", "USB DMX availability recovered"),
            ("WARNING", "USB DMX availability lost"),
            ("INFO", "USB DMX availability recovered"),
            ("WARNING", "USB DMX availability lost"),
        ]

    asyncio.run(exercise())


def test_mutation_during_reconnect_does_not_start_second_worker() -> None:
    """An unavailable controller keeps one worker and replays latest state."""

    async def exercise() -> None:
        backend = FakeBackend()
        backend.send_failures = 1
        backend.reconnect_gate = asyncio.Event()
        controller = DmxController(backend, reconnect_delays=(0,))
        await controller.async_start()

        await controller.async_set_channel(1, 10)
        await backend.reconnect_started.wait()
        await controller.async_set_channel(2, 20)
        await asyncio.sleep(0)

        assert backend.reconnect_calls == 1
        backend.reconnect_gate.set()
        await _wait_until(lambda: controller.available)
        assert backend.reconnect_calls == 1
        assert backend.frames[-1] == bytes((10, 20)) + bytes(510)
        await controller.async_stop()

    asyncio.run(exercise())


def test_availability_listeners_are_isolated_and_unsubscribeable() -> None:
    """A broken or removed listener cannot corrupt controller availability."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = DmxController(backend)
        received: list[bool] = []

        def broken_listener(available: bool) -> None:  # noqa: FBT001
            raise RuntimeError(available)

        controller.add_availability_listener(broken_listener)
        unsubscribe = controller.add_availability_listener(received.append)
        await controller.async_start()
        unsubscribe()
        await controller.async_stop()

        assert received == [True]
        assert not controller.available

    asyncio.run(exercise())


def test_stop_cancels_owned_transition_and_reconnect_tasks() -> None:
    """Stopping cancels background work before disconnecting the backend."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = _RecordingTaskController(backend)
        await controller.async_start()
        await controller.async_transition_channel(1, 255, 60)
        backend.send_failures = 1
        await controller.async_set_channel(2, 10)

        await controller.async_stop()
        frame_at_stop = controller.current_frame

        assert controller.current_frame == frame_at_stop
        assert not controller.available
        assert backend.disconnect_calls == 2
        assert all(task.done() for task in controller.created_tasks)

    asyncio.run(exercise())


def test_stop_can_send_optional_blackout_before_disconnect() -> None:
    """Optional clean-shutdown blackout sends one all-zero frame."""

    async def exercise() -> None:
        backend = FakeBackend()
        controller = DmxController(backend)
        await controller.async_start()
        await controller.async_set_channel(1, 255)

        await controller.async_stop(blackout=True)

        assert controller.current_frame == bytes(512)
        assert backend.frames[-1] == bytes(512)
        assert backend.disconnect_calls == 1

    asyncio.run(exercise())


def test_stop_contains_expected_backend_disconnect_failure() -> None:
    """A clean stop becomes unavailable even when closing the handle fails."""

    async def exercise() -> None:
        backend = FakeBackend()
        backend.disconnect_failures = 1
        controller = DmxController(backend)
        await controller.async_start()

        await controller.async_stop()

        assert not controller.available
        assert backend.disconnect_calls == 1

    asyncio.run(exercise())
