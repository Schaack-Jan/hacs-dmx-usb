"""Shared pytest configuration for USB DMX tests."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from types import MappingProxyType
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from homeassistant.config_entries import ConfigSubentry
from pytest_homeassistant_custom_component.common import (
    MockConfigEntry,
    MockModule,
    mock_integration,
)

from custom_components.usb_dmx.const import (
    BACKEND_SERIAL_PRO,
    CONF_BACKEND,
    CONF_DEVICE,
    CONF_INTERFACE_ID,
    CONF_STARTUP_BEHAVIOR,
    DOMAIN,
)
from custom_components.usb_dmx.models import StartupBehavior

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


@pytest.fixture
def make_fixture_subentry() -> Callable[..., ConfigSubentry]:
    """Return a factory for valid stored fixture subentries."""

    def make(
        *,
        fixture_id: str | None = None,
        unique_id: str | None = None,
        fixture_type: str = "dimmer",
        name: str = "Fixture",
        address: int = 1,
        minimum: int = 0,
        maximum: int = 255,
    ) -> ConfigSubentry:
        stored_id = fixture_id or str(uuid4())
        return ConfigSubentry(
            data=MappingProxyType(
                {
                    "id": stored_id,
                    "type": fixture_type,
                    "name": name,
                    "address": address,
                    "minimum": minimum,
                    "maximum": maximum,
                }
            ),
            subentry_type="fixture",
            title=name,
            unique_id=unique_id if unique_id is not None else stored_id,
        )

    return make


@pytest.fixture
def make_usb_dmx_entry() -> Callable[..., MockConfigEntry]:
    """Return a factory for USB DMX entries with fixture subentries."""

    def make(
        *subentries: ConfigSubentry,
        startup_behavior: StartupBehavior = StartupBehavior.RESTORE,
        title: str = "Studio Interface",
    ) -> MockConfigEntry:
        return MockConfigEntry(
            domain=DOMAIN,
            data={
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: "/dev/serial/by-id/dmx-test",
                CONF_INTERFACE_ID: "dmx-test",
            },
            options={CONF_STARTUP_BEHAVIOR: startup_behavior.value},
            title=title,
            unique_id="dmx-test",
            subentries_data=tuple(subentry.as_dict() for subentry in subentries),
        )

    return make
