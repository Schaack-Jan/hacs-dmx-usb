"""Abstract backend contract for USB DMX transports."""

from __future__ import annotations

from abc import ABC, abstractmethod

from ..const import DMX_CHANNEL_COUNT
from ..models import BackendCapabilities, BackendInfo


class DmxBackend(ABC):
    """Define the asynchronous contract implemented by DMX transports."""

    @property
    @abstractmethod
    def capabilities(self) -> BackendCapabilities:
        """Return immutable backend capabilities."""

    @abstractmethod
    async def connect(self) -> None:
        """Open the backend connection."""

    @abstractmethod
    async def disconnect(self) -> None:
        """Close the backend connection idempotently."""

    @abstractmethod
    async def send_frame(self, frame: bytes) -> None:
        """Send one complete DMX universe."""

    @abstractmethod
    async def probe(self) -> BackendInfo:
        """Probe the configured interface and return identifying information."""

    async def reconnect(self) -> None:
        """Replace the current connection using the default backend sequence."""
        await self.disconnect()
        await self.connect()

    @staticmethod
    def _validate_frame(frame: bytes) -> None:
        """Reject frames that do not contain exactly one DMX universe."""
        if len(frame) != DMX_CHANNEL_COUNT:
            msg = "DMX frames must contain exactly 512 bytes"
            raise ValueError(msg)
