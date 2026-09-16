"""Tests for USB DMX entry-wide options."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
from unittest.mock import patch

import pytest
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.usb_dmx.config_flow import UsbDmxConfigFlow, UsbDmxOptionsFlow
from custom_components.usb_dmx.const import (
    BACKEND_SERIAL_PRO,
    CONF_BACKEND,
    CONF_BLACKOUT_ON_SHUTDOWN,
    CONF_DEVICE,
    CONF_INTERFACE_ID,
    CONF_STARTUP_BEHAVIOR,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant import config_entries
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)


def _entry(*, options: dict[str, Any] | None = None) -> MockConfigEntry:
    """Build one parent entry with optional existing options."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/serial/by-id/dmx-test",
            CONF_INTERFACE_ID: "dmx-test",
        },
        options=options or {},
        unique_id="dmx-test",
    )


def _suggested_values(result: config_entries.ConfigFlowResult) -> dict[str, Any]:
    """Extract suggested values from a form schema."""
    return {
        marker.schema: marker.description["suggested_value"]
        for marker in result["data_schema"].schema
        if marker.description and "suggested_value" in marker.description
    }


async def _start_options_flow(
    hass: HomeAssistant, entry: MockConfigEntry
) -> config_entries.ConfigFlowResult:
    """Start the options flow for an added config entry."""
    return await hass.config_entries.options.async_init(entry.entry_id)


def test_config_flow_returns_reload_options_handler() -> None:
    """The config flow exposes the dedicated options-flow class."""
    assert isinstance(
        UsbDmxConfigFlow.async_get_options_flow(_entry()), UsbDmxOptionsFlow
    )


async def test_options_form_defaults(hass: HomeAssistant) -> None:
    """A new entry suggests restore startup and no shutdown blackout."""
    entry = _entry()
    entry.add_to_hass(hass)

    form = await _start_options_flow(hass, entry)

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "init"
    assert _suggested_values(form) == {
        CONF_STARTUP_BEHAVIOR: "restore",
        CONF_BLACKOUT_ON_SHUTDOWN: False,
    }


async def test_options_form_suggests_existing_values(hass: HomeAssistant) -> None:
    """Existing options replace defaults in the edit form."""
    entry = _entry(
        options={
            CONF_STARTUP_BEHAVIOR: "zero",
            CONF_BLACKOUT_ON_SHUTDOWN: True,
            "future_option": "preserve",
        }
    )
    entry.add_to_hass(hass)

    form = await _start_options_flow(hass, entry)

    assert _suggested_values(form) == {
        CONF_STARTUP_BEHAVIOR: "zero",
        CONF_BLACKOUT_ON_SHUTDOWN: True,
    }


@pytest.mark.parametrize(
    ("startup_behavior", "blackout"),
    [("restore", False), ("restore", True), ("zero", False), ("zero", True)],
)
async def test_options_persist_json_safe_values_and_reload_once(
    hass: HomeAssistant,
    startup_behavior: str,
    *,
    blackout: bool,
) -> None:
    """Every supported option combination persists and schedules one reload."""
    entry = _entry(
        options={
            CONF_STARTUP_BEHAVIOR: "zero"
            if startup_behavior == "restore"
            else "restore",
            CONF_BLACKOUT_ON_SHUTDOWN: not blackout,
            "future_option": "preserve",
        }
    )
    entry.add_to_hass(hass)
    form = await _start_options_flow(hass, entry)

    with patch.object(hass.config_entries, "async_schedule_reload") as reload_mock:
        result = await hass.config_entries.options.async_configure(
            form["flow_id"],
            {
                CONF_STARTUP_BEHAVIOR: startup_behavior,
                CONF_BLACKOUT_ON_SHUTDOWN: blackout,
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert dict(entry.options) == {
        CONF_STARTUP_BEHAVIOR: startup_behavior,
        CONF_BLACKOUT_ON_SHUTDOWN: blackout,
        "future_option": "preserve",
    }
    assert isinstance(entry.options[CONF_STARTUP_BEHAVIOR], str)
    assert type(entry.options[CONF_BLACKOUT_ON_SHUTDOWN]) is bool
    reload_mock.assert_called_once_with(entry.entry_id)
