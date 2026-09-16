"""Tests for the USB DMX interface config flow."""

from __future__ import annotations

from types import SimpleNamespace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.usb import UsbServiceInfo
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.usb_dmx.const import (
    BACKEND_SERIAL_PRO,
    CONF_BACKEND,
    CONF_DEVICE,
    CONF_INTERFACE_ID,
    DOMAIN,
)

from .fakes import FakeBackend

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency", "_mock_entry_setup"
)

MANUAL_PATH = "__manual_path__"


async def _start_user_flow(hass: HomeAssistant) -> config_entries.ConfigFlowResult:
    """Start a user flow with custom integrations enabled by the caller."""
    return await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )


async def test_candidate_setup_uses_selector_metadata_and_stable_by_id(
    hass: HomeAssistant,
) -> None:
    """A scanned candidate stores the by-id path and product-derived identity."""
    backend = FakeBackend()
    candidate = SimpleNamespace(
        device="/dev/ttyUSB0",
        resolved_device=None,
        serial_number="SERIAL-1",
        manufacturer="ENTTEC",
        description="DMX USB Pro",
    )

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value="/dev/serial/by-id/dmx-serial-1"),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ) as create_backend,
    ):
        form = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: "/dev/serial/by-id/dmx-serial-1",
            },
        )

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "user"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BACKEND: BACKEND_SERIAL_PRO,
        CONF_DEVICE: "/dev/serial/by-id/dmx-serial-1",
        CONF_INTERFACE_ID: "enttec:dmx usb pro:serial-1",
    }
    assert result["result"].unique_id == "enttec:dmx usb pro:serial-1"
    assert (
        create_backend.await_args.args[0]
        .data[CONF_DEVICE]
        .startswith("/dev/serial/by-id/")
    )
    assert backend.connect_calls == 1
    assert backend.disconnect_calls == 1
    assert not backend.connected


async def test_manual_path_escape_hatch_creates_entry(
    hass: HomeAssistant,
) -> None:
    """The candidate selector exposes a raw manual-path setup route."""
    backend = FakeBackend()
    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
    ):
        form = await _start_user_flow(hass)
        manual_form = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        result = await hass.config_entries.flow.async_configure(
            manual_form["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: " /dev/ttyUSB9 ",
            },
        )

    assert manual_form["type"] is FlowResultType.FORM
    assert manual_form["step_id"] == "manual"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BACKEND: BACKEND_SERIAL_PRO,
        CONF_DEVICE: "/dev/ttyUSB9",
        CONF_INTERFACE_ID: "/dev/ttyUSB9",
    }
    assert result["result"].unique_id == "/dev/ttyUSB9"
    assert backend.disconnect_calls == 1


async def test_manual_path_rejects_empty_and_unreachable_devices(
    hass: HomeAssistant,
) -> None:
    """Manual setup keeps stable field/base errors and always closes validation."""
    backend = FakeBackend()
    backend.connect_failure_after_open = True
    backend.connect_failures = 1
    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
    ):
        form = await _start_user_flow(hass)
        manual_form = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        empty = await hass.config_entries.flow.async_configure(
            manual_form["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "   "},
        )
        unreachable = await hass.config_entries.flow.async_configure(
            empty["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB404"},
        )

    assert empty["type"] is FlowResultType.FORM
    assert empty["errors"] == {CONF_DEVICE: "invalid_device"}
    assert unreachable["type"] is FlowResultType.FORM
    assert unreachable["errors"] == {"base": "cannot_connect"}
    assert backend.connect_calls == 1
    assert backend.disconnect_calls == 1
    assert not backend.connected


async def test_duplicate_manual_path_aborts_before_opening_device(
    hass: HomeAssistant,
) -> None:
    """A normalized manual path cannot create a duplicate physical interface."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/ttyUSB0",
            CONF_INTERFACE_ID: "/dev/ttyUSB0",
        },
        unique_id="/dev/ttyUSB0",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            new=AsyncMock(),
        ) as create_backend,
    ):
        form = await _start_user_flow(hass)
        manual_form = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        result = await hass.config_entries.flow.async_configure(
            manual_form["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev//ttyUSB0"},
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    create_backend.assert_not_awaited()


async def test_usb_discovery_requires_confirmation_and_prefers_by_id(
    hass: HomeAssistant,
) -> None:
    """Forwarded USB discovery derives stable identity and waits for confirmation."""
    backend = FakeBackend()
    discovery = UsbServiceInfo(
        device="/dev/ttyUSB0",
        vid="0403",
        pid="6001",
        serial_number="ABC123",
        manufacturer="ENTTEC",
        description="DMX USB Pro",
    )

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value="/dev/serial/by-id/enttec-abc123"),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
    ):
        form = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USB},
            data=discovery,
        )
        assert hass.config_entries.async_entries(DOMAIN) == []
        result = await hass.config_entries.flow.async_configure(form["flow_id"], {})

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "usb_confirm"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BACKEND: BACKEND_SERIAL_PRO,
        CONF_DEVICE: "/dev/serial/by-id/enttec-abc123",
        CONF_INTERFACE_ID: "enttec:dmx usb pro:abc123",
    }
    assert result["result"].unique_id == "enttec:dmx usb pro:abc123"
    assert backend.disconnect_calls == 1


async def test_reconfigure_updates_connection_only_and_preserves_identity(
    hass: HomeAssistant,
) -> None:
    """Reconfigure reloads new connection data without replacing entry identity."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        entry_id="entry-identity",
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/ttyUSB0",
            CONF_INTERFACE_ID: "stable-interface",
            "future_setting": "preserve",
        },
        options={"option": "preserve"},
        unique_id="stable-interface",
    )
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(hass.config_entries, "async_schedule_reload") as reload_mock,
    ):
        form = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: "/dev/serial/by-id/replacement",
            },
        )

    assert form["type"] is FlowResultType.FORM
    assert form["step_id"] == "reconfigure"
    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.entry_id == "entry-identity"
    assert entry.unique_id == "stable-interface"
    assert dict(entry.data) == {
        CONF_BACKEND: BACKEND_SERIAL_PRO,
        CONF_DEVICE: "/dev/serial/by-id/replacement",
        CONF_INTERFACE_ID: "stable-interface",
        "future_setting": "preserve",
    }
    assert dict(entry.options) == {"option": "preserve"}
    reload_mock.assert_called_once_with(entry.entry_id)
    assert backend.disconnect_calls == 1
