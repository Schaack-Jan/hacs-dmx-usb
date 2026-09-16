"""Tests for the DMX USB Pro serial protocol backend."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from serialx import SerialException

from custom_components.usb_dmx.backends.base import (
    BackendConnectionError,
    BackendNotConnectedError,
    BackendTransportError,
)
from custom_components.usb_dmx.backends.serial_pro import (
    SerialProBackend,
    encode_dmx_usb_pro_frame,
)


class _RecordingWriter:
    """Record serial writer lifecycle and optionally fail writes."""

    def __init__(self, *, drain_error: BaseException | None = None) -> None:
        self.calls: list[object] = []
        self.drain_error = drain_error

    def write(self, data: bytes) -> None:
        self.calls.append(("write", data))

    async def drain(self) -> None:
        self.calls.append("drain")
        if self.drain_error is not None:
            raise self.drain_error

    def close(self) -> None:
        self.calls.append("close")

    async def wait_closed(self) -> None:
        self.calls.append("wait_closed")


def test_encoder_builds_exact_label_6_packet() -> None:
    """Encoding emits one exact 518-byte DMX USB Pro label-6 packet."""
    frame = bytes(index % 256 for index in range(512))

    packet = encode_dmx_usb_pro_frame(frame)

    assert len(packet) == 518
    assert packet[:5] == bytes((0x7E, 0x06, 0x01, 0x02, 0x00))
    assert packet[5:517] == frame
    assert packet[-1] == 0xE7


@pytest.mark.parametrize("length", [0, 511, 513])
def test_encoder_rejects_non_universe_frame_lengths(length: int) -> None:
    """Encoding refuses partial and oversized DMX universes."""
    with pytest.raises(ValueError, match="exactly 512 bytes"):
        encode_dmx_usb_pro_frame(bytes(length))


def test_backend_connects_and_writes_complete_packet() -> None:
    """A connected backend writes and drains an encoded frame."""
    writer = _RecordingWriter()
    connection_calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    async def serial_factory(
        *args: Any, **kwargs: Any
    ) -> tuple[object, _RecordingWriter]:
        connection_calls.append((args, kwargs))
        return object(), writer

    async def exercise() -> None:
        backend = SerialProBackend("/dev/serial/by-id/dmx", serial_factory)
        await backend.connect()
        await backend.send_frame(bytes((255,)) + bytes(511))

    asyncio.run(exercise())

    assert connection_calls == [(("/dev/serial/by-id/dmx",), {"baudrate": 57600})]
    packet = encode_dmx_usb_pro_frame(bytes((255,)) + bytes(511))
    assert writer.calls == [("write", packet), "drain"]


def test_backend_disconnect_is_idempotent() -> None:
    """Disconnect closes a retained writer exactly once."""
    writer = _RecordingWriter()

    async def serial_factory(
        *_args: Any, **_kwargs: Any
    ) -> tuple[object, _RecordingWriter]:
        return object(), writer

    async def exercise() -> None:
        backend = SerialProBackend("/dev/ttyUSB0", serial_factory)
        await backend.connect()
        await backend.disconnect()
        await backend.disconnect()

    asyncio.run(exercise())

    assert writer.calls == ["close", "wait_closed"]


def test_backend_rejects_send_before_connect() -> None:
    """Sending without a live writer reports a focused backend error."""

    async def serial_factory(*_args: Any, **_kwargs: Any) -> tuple[object, object]:
        raise AssertionError

    backend = SerialProBackend("/dev/ttyUSB0", serial_factory)

    with pytest.raises(BackendNotConnectedError):
        asyncio.run(backend.send_frame(bytes(512)))


@pytest.mark.parametrize(
    "native_error",
    [OSError("missing"), TimeoutError("slow"), SerialException("protocol")],
)
def test_backend_wraps_connection_failures(native_error: BaseException) -> None:
    """Native connection failures retain their cause behind a typed error."""

    async def serial_factory(*_args: Any, **_kwargs: Any) -> tuple[object, object]:
        raise native_error

    backend = SerialProBackend("/dev/ttyUSB0", serial_factory)

    with pytest.raises(BackendConnectionError) as error:
        asyncio.run(backend.connect())

    assert error.value.__cause__ is native_error


@pytest.mark.parametrize(
    "native_error",
    [OSError("unplugged"), TimeoutError("slow"), SerialException("protocol")],
)
def test_backend_wraps_transport_failures(native_error: BaseException) -> None:
    """Native write failures retain their cause behind a typed error."""
    writer = _RecordingWriter(drain_error=native_error)

    async def serial_factory(
        *_args: Any, **_kwargs: Any
    ) -> tuple[object, _RecordingWriter]:
        return object(), writer

    async def exercise() -> None:
        backend = SerialProBackend("/dev/ttyUSB0", serial_factory)
        await backend.connect()
        await backend.send_frame(bytes(512))

    with pytest.raises(BackendTransportError) as error:
        asyncio.run(exercise())

    assert error.value.__cause__ is native_error


def test_backend_preserves_programming_errors() -> None:
    """An invalid frame remains a ValueError rather than a transport error."""
    backend = SerialProBackend("/dev/ttyUSB0")

    with pytest.raises(ValueError, match="exactly 512 bytes"):
        asyncio.run(backend.send_frame(bytes(511)))
