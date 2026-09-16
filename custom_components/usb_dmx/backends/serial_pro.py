"""DMX USB Pro protocol backend using serialx."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any, Final, Protocol

import serialx

from ..const import DMX_CHANNEL_COUNT
from ..models import BackendCapabilities, BackendInfo
from .base import (
    BackendConnectionError,
    BackendNotConnectedError,
    BackendTransportError,
    DmxBackend,
)

_START_OF_MESSAGE: Final = 0x7E
_SEND_DMX_LABEL: Final = 0x06
_DMX_START_CODE: Final = 0x00
_END_OF_MESSAGE: Final = 0xE7
_PAYLOAD_LENGTH: Final = DMX_CHANNEL_COUNT + 1
_BAUDRATE: Final = 57600


class _SerialWriter(Protocol):
    """Describe the serialx writer operations used by the backend."""

    def write(self, data: bytes) -> None:
        """Queue bytes for transmission."""

    async def drain(self) -> None:
        """Wait until queued bytes have been transmitted."""

    def close(self) -> None:
        """Begin closing the serial transport."""

    async def wait_closed(self) -> None:
        """Wait until the serial transport is closed."""


type SerialFactory = Callable[..., Awaitable[tuple[Any, _SerialWriter]]]


def encode_dmx_usb_pro_frame(frame: bytes) -> bytes:
    """Encode one universe as a DMX USB Pro label-6 packet."""
    if len(frame) != DMX_CHANNEL_COUNT:
        msg = "DMX frames must contain exactly 512 bytes"
        raise ValueError(msg)

    return (
        bytes(
            (
                _START_OF_MESSAGE,
                _SEND_DMX_LABEL,
                _PAYLOAD_LENGTH & 0xFF,
                _PAYLOAD_LENGTH >> 8,
                _DMX_START_CODE,
            )
        )
        + frame
        + bytes((_END_OF_MESSAGE,))
    )


class SerialProBackend(DmxBackend):
    """Send complete DMX universes over a DMX USB Pro serial interface."""

    capabilities = BackendCapabilities(refresh_mode="hardware")

    def __init__(
        self,
        path: str,
        serial_factory: SerialFactory = serialx.open_serial_connection,
    ) -> None:
        """Initialize a backend for one serial device path."""
        self._path = path
        self._serial_factory = serial_factory
        self._reader: Any | None = None
        self._writer: _SerialWriter | None = None
        self._write_lock = asyncio.Lock()

    async def connect(self) -> None:
        """Open the serial device and retain its reader and writer."""
        if self._writer is not None:
            return

        try:
            reader, writer = await self._serial_factory(self._path, baudrate=_BAUDRATE)
        except (OSError, serialx.SerialException) as err:
            msg = f"Unable to connect to DMX interface at {self._path}"
            raise BackendConnectionError(msg) from err

        self._reader = reader
        self._writer = writer

    async def disconnect(self) -> None:
        """Close the retained writer idempotently."""
        async with self._write_lock:
            writer = self._writer
            self._reader = None
            self._writer = None
            if writer is None:
                return

            try:
                writer.close()
                await writer.wait_closed()
            except (OSError, serialx.SerialException) as err:
                msg = f"Unable to close DMX interface at {self._path}"
                raise BackendConnectionError(msg) from err

    async def send_frame(self, frame: bytes) -> None:
        """Encode, write, and drain one complete DMX universe."""
        self._validate_frame(frame)
        packet = encode_dmx_usb_pro_frame(frame)

        async with self._write_lock:
            writer = self._writer
            if writer is None:
                msg = "DMX interface is not connected"
                raise BackendNotConnectedError(msg)

            try:
                writer.write(packet)
                await writer.drain()
            except (OSError, serialx.SerialException) as err:
                msg = f"Unable to send DMX frame to {self._path}"
                raise BackendTransportError(msg) from err

    async def probe(self) -> BackendInfo:
        """Return identifying information for the configured interface."""
        return BackendInfo(name="serial_pro", device=self._path)
