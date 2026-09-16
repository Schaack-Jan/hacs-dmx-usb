"""Stateful DMX universe controller."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import math
from collections.abc import Callable, Coroutine, Sequence
from datetime import UTC, datetime
from typing import Any, Final

from .backends.base import BackendError, DmxBackend
from .const import DMX_CHANNEL_COUNT, DMX_MAX_VALUE

_LOGGER = logging.getLogger(__name__)
_TRANSITION_STEPS_PER_SECOND: Final = 40

type AvailabilityListener = Callable[[bool], None]
type UnsubscribeCallback = Callable[[], None]


class DmxController:
    """Own one DMX universe and serialize all backend writes."""

    def __init__(
        self,
        backend: DmxBackend,
        *,
        reconnect_delays: Sequence[float] = (1, 2, 5, 10, 30),
    ) -> None:
        """Initialize a deterministic zeroed universe."""
        delays = tuple(reconnect_delays)
        if not delays or any(delay < 0 for delay in delays):
            msg = "Reconnect delays must be a non-empty sequence of non-negative values"
            raise ValueError(msg)

        self._backend = backend
        self._reconnect_delays = delays
        self._frame = bytearray(DMX_CHANNEL_COUNT)
        self._lock = asyncio.Lock()
        self._available = False
        self._stopping = False
        self._availability_listeners: list[AvailabilityListener] = []
        self._transition_tasks: dict[int, asyncio.Task[None]] = {}
        self._reconnect_task: asyncio.Task[None] | None = None
        self._last_successful_frame_timestamp: datetime | None = None
        self._reconnect_count = 0

    @property
    def available(self) -> bool:
        """Return whether the backend currently accepts frames."""
        return self._available

    @property
    def current_frame(self) -> bytes:
        """Return an immutable snapshot of the current universe."""
        return bytes(self._frame)

    @property
    def last_successful_frame_timestamp(self) -> datetime | None:
        """Return the UTC timestamp of the last successful complete frame."""
        return self._last_successful_frame_timestamp

    @property
    def reconnect_count(self) -> int:
        """Return the number of reconnect attempts made by this controller."""
        return self._reconnect_count

    def add_availability_listener(
        self, listener: AvailabilityListener
    ) -> UnsubscribeCallback:
        """Register an availability listener and return its unsubscribe callback."""
        self._availability_listeners.append(listener)

        def unsubscribe() -> None:
            with contextlib.suppress(ValueError):
                self._availability_listeners.remove(listener)

        return unsubscribe

    async def async_start(self) -> None:
        """Connect the backend and publish availability."""
        if self._available:
            return
        self._stopping = False
        await self._backend.connect()
        self._set_available(available=True)

    async def async_stop(self, *, blackout: bool = False) -> None:
        """Cancel owned work, optionally black out, and close the backend."""
        self._stopping = True
        await self._cancel_all_transitions()
        await self._cancel_reconnect()

        if blackout:
            await self._async_blackout_without_task_cancellation()

        with contextlib.suppress(BackendError):
            await self._backend.disconnect()
        self._set_available(available=False)

    async def async_set_channel(self, address: int, value: int) -> None:
        """Set one one-based DMX address and send the complete universe."""
        self._validate_channel(address, value)
        await self._cancel_transition(address)
        await self._async_set_channel_value(address, value)

    async def async_transition_channel(
        self, address: int, value: int, duration: float
    ) -> None:
        """Replace any address transition and interpolate from its current value."""
        self._validate_channel(address, value)
        if isinstance(duration, bool) or not isinstance(duration, (int, float)):
            msg = "DMX transition duration must be a non-negative number"
            raise TypeError(msg)
        if not math.isfinite(duration) or duration < 0:
            msg = "DMX transition duration must be a non-negative number"
            raise ValueError(msg)
        if duration == 0:
            await self.async_set_channel(address, value)
            return

        await self._cancel_transition(address)
        async with self._lock:
            start_value = self._frame[address - 1]

        task = self._create_task(
            self._async_run_transition(address, start_value, value, duration),
            name=f"usb_dmx_transition_{address}",
        )
        self._transition_tasks[address] = task

    async def async_send_current_frame(self) -> None:
        """Send an immutable snapshot of the complete current universe."""
        async with self._lock:
            if self._available:
                await self._async_send_locked()
            else:
                self._schedule_reconnect()

    async def async_blackout(self) -> None:
        """Cancel transitions, set every slot to zero, and send the universe."""
        await self._cancel_all_transitions()
        await self._async_blackout_without_task_cancellation()

    async def _async_blackout_without_task_cancellation(self) -> None:
        async with self._lock:
            self._frame[:] = bytes(DMX_CHANNEL_COUNT)
            if self._available:
                await self._async_send_locked()
            else:
                self._schedule_reconnect()

    async def _async_set_channel_value(self, address: int, value: int) -> None:
        async with self._lock:
            self._frame[address - 1] = value
            if self._available:
                await self._async_send_locked()
            else:
                self._schedule_reconnect()

    async def _async_send_locked(self) -> None:
        try:
            await self._backend.send_frame(bytes(self._frame))
        except BackendError:
            self._set_available(available=False)
            with contextlib.suppress(BackendError):
                await self._backend.disconnect()
            self._schedule_reconnect()
        else:
            self._last_successful_frame_timestamp = datetime.now(UTC)

    async def _async_run_transition(
        self, address: int, start: int, target: int, duration: float
    ) -> None:
        task = asyncio.current_task()
        steps = max(1, math.ceil(duration * _TRANSITION_STEPS_PER_SECOND))
        interval = duration / steps
        last_value = start
        try:
            for step in range(1, steps + 1):
                await asyncio.sleep(interval)
                value = round(start + (target - start) * step / steps)
                if value != last_value:
                    await self._async_set_channel_value(address, value)
                    last_value = value
        finally:
            if self._transition_tasks.get(address) is task:
                self._transition_tasks.pop(address, None)

    async def _async_reconnect(self) -> None:
        task = asyncio.current_task()
        delay_index = 0
        try:
            while not self._stopping and not self._available:
                delay = self._reconnect_delays[
                    min(delay_index, len(self._reconnect_delays) - 1)
                ]
                delay_index += 1
                await asyncio.sleep(delay)
                if self._stopping:
                    return

                self._reconnect_count += 1
                try:
                    await self._backend.reconnect()
                    async with self._lock:
                        await self._backend.send_frame(bytes(self._frame))
                        self._last_successful_frame_timestamp = datetime.now(UTC)
                        self._set_available(available=True)
                except BackendError:
                    self._set_available(available=False)
                    with contextlib.suppress(BackendError):
                        await self._backend.disconnect()
        finally:
            if self._reconnect_task is task:
                self._reconnect_task = None

    def _schedule_reconnect(self) -> None:
        if self._stopping:
            return
        if self._reconnect_task is not None and not self._reconnect_task.done():
            return
        self._reconnect_task = self._create_task(
            self._async_reconnect(), name="usb_dmx_reconnect"
        )

    async def _cancel_transition(self, address: int) -> None:
        task = self._transition_tasks.pop(address, None)
        await self._cancel_task(task)

    async def _cancel_all_transitions(self) -> None:
        tasks = tuple(self._transition_tasks.values())
        self._transition_tasks.clear()
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _cancel_reconnect(self) -> None:
        task = self._reconnect_task
        self._reconnect_task = None
        await self._cancel_task(task)

    @staticmethod
    async def _cancel_task(task: asyncio.Task[None] | None) -> None:
        if task is None or task.done():
            return
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    @staticmethod
    def _create_task(
        coroutine: Coroutine[Any, Any, None], *, name: str
    ) -> asyncio.Task[None]:
        return asyncio.create_task(coroutine, name=name)

    def _set_available(self, *, available: bool) -> None:
        if self._available == available:
            return
        self._available = available
        for listener in tuple(self._availability_listeners):
            try:
                listener(available)
            except Exception:
                _LOGGER.exception("USB DMX availability listener failed")

    @staticmethod
    def _validate_channel(address: int, value: int) -> None:
        if type(address) is not int or not 1 <= address <= DMX_CHANNEL_COUNT:
            msg = "DMX address must be between 1 and 512"
            raise ValueError(msg)
        if type(value) is not int or not 0 <= value <= DMX_MAX_VALUE:
            msg = "DMX value must be between 0 and 255"
            raise ValueError(msg)
