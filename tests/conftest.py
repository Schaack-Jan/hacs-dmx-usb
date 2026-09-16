"""Shared pytest configuration for USB DMX tests."""

from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from pytest_homeassistant_custom_component.common import MockModule, mock_integration

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


@pytest.fixture
def _mock_usb_dependency(hass: HomeAssistant) -> None:
    """Avoid starting the host USB watcher in config-flow unit tests."""
    mock_integration(hass, MockModule("usb"))


@pytest.fixture
def _mock_entry_setup() -> Iterator[AsyncMock]:
    """Prevent config-flow entry creation from opening real hardware."""
    with patch(
        "custom_components.usb_dmx.async_setup_entry",
        AsyncMock(return_value=True),
    ) as setup:
        yield setup
