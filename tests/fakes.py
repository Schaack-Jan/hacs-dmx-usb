"""Reusable test doubles for USB DMX runtime tests."""

from __future__ import annotations

import asyncio

from custom_components.usb_dmx.backends.base import (
    BackendConnectionError,
    BackendTransportError,
    DmxBackend,
)
from custom_components.usb_dmx.models import BackendCapabilities, BackendInfo


class FakeBackend(DmxBackend):
    """Controllable backend that records complete frames."""

    capabilities = BackendCapabilities(refresh_mode="hardware")

    def __init__(self) -> None:
        """Initialize a connected-state and call recorder."""
        self.connected = False
        self.connect_calls = 0
        self.disconnect_calls = 0
        self.reconnect_calls = 0
        self.send_attempts = 0
        self.frames: list[bytes] = []
        self.send_failures = 0
        self.disconnect_failures = 0
        self.reconnect_failures = 0
        self.send_started = asyncio.Event()
        self.send_gate: asyncio.Event | None = None
        self.reconnect_started = asyncio.Event()
        self.reconnect_gate: asyncio.Event | None = None

    async def connect(self) -> None:
        """Record a successful connection."""
        self.connect_calls += 1
        self.connected = True

    async def disconnect(self) -> None:
        """Record an idempotent disconnect."""
        self.disconnect_calls += 1
        self.connected = False
        if self.disconnect_failures:
            self.disconnect_failures -= 1
            raise BackendConnectionError

    async def reconnect(self) -> None:
        """Wait at an optional gate, then reconnect or fail."""
        self.reconnect_calls += 1
        self.reconnect_started.set()
        if self.reconnect_gate is not None:
            await self.reconnect_gate.wait()
        if self.reconnect_failures:
            self.reconnect_failures -= 1
            raise BackendConnectionError
        self.connected = True

    async def send_frame(self, frame: bytes) -> None:
        """Record a complete frame or raise a configured failure."""
        self._validate_frame(frame)
        self.send_attempts += 1
        self.send_started.set()
        if self.send_gate is not None:
            await self.send_gate.wait()
        if self.send_failures:
            self.send_failures -= 1
            raise BackendTransportError
        if not self.connected:
            raise BackendTransportError
        self.frames.append(bytes(frame))

    async def probe(self) -> BackendInfo:
        """Return stable fake backend information."""
        return BackendInfo(name="fake", device="memory://dmx")
