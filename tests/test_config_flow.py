"""Tests for the USB DMX interface config flow."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
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


async def test_device_selector_localizes_manual_choice_without_rewriting_paths(
    hass: HomeAssistant,
) -> None:
    """Frontend value lookup localizes manual while discovered paths stay exact."""
    candidate = SimpleNamespace(
        device="/dev/ttyUSB0",
        resolved_device=None,
        serial_number="SERIAL-1",
        manufacturer="ENTTEC",
        description="DMX USB Pro",
    )
    stable_path = "/dev/serial/by-id/dmx-serial-1"

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[candidate]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value=stable_path),
        ),
    ):
        form = await _start_user_flow(hass)

    selector = form["data_schema"].schema[CONF_DEVICE]
    assert selector.config["translation_key"] == "device"
    assert selector.config["options"] == [
        {"value": stable_path, "label": stable_path},
        {"value": MANUAL_PATH, "label": "Manual entry"},
    ]
    discovered_option, manual_option = selector.config["options"]
    translation_root = (
        Path(__file__).parents[1] / "custom_components" / "usb_dmx" / "translations"
    )
    for language, expected_manual_label in (
        ("en", "Enter a serial device path manually"),
        ("de", "Pfad zum seriellen Gerät manuell eingeben"),
    ):
        translations = json.loads(
            (translation_root / f"{language}.json").read_text(encoding="utf-8")
        )
        translated_options = translations["selector"][
            selector.config["translation_key"]
        ]["options"]

        assert (
            translated_options.get(manual_option["value"], manual_option["label"])
            == expected_manual_label
        )
        assert (
            translated_options.get(
                discovered_option["value"], discovered_option["label"]
            )
            == stable_path
        )


async def test_manual_path_escape_hatch_creates_entry(
    hass: HomeAssistant,
) -> None:
    """A raw manual path is replaced by HA's stable by-id path before storage."""
    backend = FakeBackend()
    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value="/dev/serial/by-id/manual-dmx"),
        ) as get_stable_path,
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
                CONF_DEVICE: " /dev//ttyUSB9 ",
            },
        )

    assert manual_form["type"] is FlowResultType.FORM
    assert manual_form["step_id"] == "manual"
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {
        CONF_BACKEND: BACKEND_SERIAL_PRO,
        CONF_DEVICE: "/dev/serial/by-id/manual-dmx",
        CONF_INTERFACE_ID: "/dev/serial/by-id/manual-dmx",
    }
    assert result["result"].unique_id == "/dev/serial/by-id/manual-dmx"
    get_stable_path.assert_awaited_once_with(hass, "/dev/ttyUSB9")
    assert backend.disconnect_calls == 1


async def test_manual_path_collapses_posix_double_slash(
    hass: HomeAssistant,
) -> None:
    """A POSIX path with two leading slashes stores one canonical leading slash."""
    backend = FakeBackend()
    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value="//dev/ttyUSB9"),
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
                CONF_DEVICE: "//dev//ttyUSB9",
            },
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE] == "/dev/ttyUSB9"
    assert result["result"].unique_id == "/dev/ttyUSB9"


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
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(side_effect=lambda _hass, path: path),
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
    tmp_path: Path,
) -> None:
    """A manual real-path alias cannot duplicate a discovered by-id interface."""
    device = tmp_path / "ttyUSB0"
    device.touch()
    by_id = tmp_path / "serial" / "by-id"
    by_id.mkdir(parents=True)
    stable_path = by_id / "dmx-interface"
    stable_path.symlink_to(device)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: str(stable_path),
            CONF_INTERFACE_ID: "enttec:dmx usb pro:serial-1",
        },
        unique_id="enttec:dmx usb pro:serial-1",
    )
    entry.add_to_hass(hass)

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(side_effect=lambda _hass, path: path),
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
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: f"{tmp_path}//ttyUSB0",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    create_backend.assert_not_awaited()


async def test_scanned_candidate_cannot_duplicate_manual_entry(
    hass: HomeAssistant,
) -> None:
    """Product metadata cannot bypass a device-path duplicate before opening."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/serial/by-id/dmx-interface",
            CONF_INTERFACE_ID: "/dev/serial/by-id/dmx-interface",
        },
        unique_id="/dev/serial/by-id/dmx-interface",
    )
    entry.add_to_hass(hass)
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
            AsyncMock(return_value="/dev/serial/by-id/dmx-interface"),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            new=AsyncMock(),
        ) as create_backend,
    ):
        form = await _start_user_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: "/dev/serial/by-id/dmx-interface",
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    create_backend.assert_not_awaited()


async def test_usb_discovery_cannot_duplicate_manual_entry(
    hass: HomeAssistant,
) -> None:
    """USB serial metadata cannot duplicate an entry for the same device path."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/serial/by-id/dmx-interface",
            CONF_INTERFACE_ID: "/dev/serial/by-id/dmx-interface",
        },
        unique_id="/dev/serial/by-id/dmx-interface",
    )
    entry.add_to_hass(hass)
    discovery = UsbServiceInfo(
        device="/dev/ttyUSB0",
        vid="0403",
        pid="6001",
        serial_number="SERIAL-1",
        manufacturer="ENTTEC",
        description="DMX USB Pro",
    )

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value="/dev/serial/by-id/dmx-interface"),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            new=AsyncMock(),
        ) as create_backend,
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_USB},
            data=discovery,
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    create_backend.assert_not_awaited()


async def test_manual_flow_retries_different_path_after_connection_failure(
    hass: HomeAssistant,
) -> None:
    """One manual flow can recover from cannot-connect with a different device."""
    backend = FakeBackend()
    backend.connect_failures = 1
    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(
                side_effect=[
                    "/dev/serial/by-id/unreachable",
                    "/dev/serial/by-id/reachable",
                ]
            ),
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
        cannot_connect = await hass.config_entries.flow.async_configure(
            manual_form["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB0"},
        )
        result = await hass.config_entries.flow.async_configure(
            cannot_connect["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB1"},
        )

    assert cannot_connect["type"] is FlowResultType.FORM
    assert cannot_connect["errors"] == {"base": "cannot_connect"}
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_DEVICE] == "/dev/serial/by-id/reachable"
    assert result["result"].unique_id == "/dev/serial/by-id/reachable"


async def test_failed_validation_does_not_reserve_interface_identity(
    hass: HomeAssistant,
) -> None:
    """A failed flow does not block another flow from validating the same device."""
    backend = FakeBackend()
    backend.connect_failures = 1
    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(side_effect=lambda _hass, path: path),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
    ):
        first = await _start_user_flow(hass)
        first_manual = await hass.config_entries.flow.async_configure(
            first["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        cannot_connect = await hass.config_entries.flow.async_configure(
            first_manual["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB0"},
        )

        second = await _start_user_flow(hass)
        second_manual = await hass.config_entries.flow.async_configure(
            second["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        result = await hass.config_entries.flow.async_configure(
            second_manual["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB0"},
        )

    assert cannot_connect["type"] is FlowResultType.FORM
    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_in_progress_manual_flow_reserves_canonical_device_path(
    hass: HomeAssistant,
) -> None:
    """A scanned flow cannot open a canonical device held by a manual flow."""
    candidate = SimpleNamespace(
        device="/dev/ttyUSB0",
        resolved_device=None,
        serial_number="SERIAL-1",
        manufacturer="ENTTEC",
        description="DMX USB Pro",
    )
    manual_backend = FakeBackend()
    manual_backend.connect_gate = asyncio.Event()
    scanned_backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(side_effect=[[], [candidate]]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value="/dev/serial/by-id/dmx-interface"),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(side_effect=[manual_backend, scanned_backend]),
        ),
    ):
        manual = await _start_user_flow(hass)
        manual_form = await hass.config_entries.flow.async_configure(
            manual["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        manual_result_task = asyncio.create_task(
            hass.config_entries.flow.async_configure(
                manual_form["flow_id"],
                {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB0"},
            )
        )
        await manual_backend.connect_started.wait()

        scanned = await _start_user_flow(hass)
        scanned_result = await hass.config_entries.flow.async_configure(
            scanned["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: "/dev/serial/by-id/dmx-interface",
            },
        )

        manual_backend.connect_gate.set()
        manual_result = await manual_result_task

    assert scanned_result["type"] is FlowResultType.ABORT
    assert scanned_result["reason"] == "already_in_progress"
    assert scanned_backend.connect_calls == 0
    assert manual_result["type"] is FlowResultType.CREATE_ENTRY


async def test_in_progress_scanned_flow_reserves_product_identity(
    hass: HomeAssistant,
) -> None:
    """A product identity in progress cannot open a second canonical device."""
    first_candidate = SimpleNamespace(
        device="/dev/ttyUSB0",
        resolved_device=None,
        serial_number="SERIAL-1",
        manufacturer="ENTTEC",
        description="DMX USB Pro",
    )
    second_candidate = SimpleNamespace(
        device="/dev/ttyUSB1",
        resolved_device=None,
        serial_number="SERIAL-1",
        manufacturer="ENTTEC",
        description="DMX USB Pro",
    )
    first_backend = FakeBackend()
    first_backend.connect_gate = asyncio.Event()
    second_backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(side_effect=[[first_candidate], [second_candidate]]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(
                side_effect=[
                    "/dev/serial/by-id/dmx-interface-1",
                    "/dev/serial/by-id/dmx-interface-2",
                ]
            ),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(side_effect=[first_backend, second_backend]),
        ),
    ):
        first = await _start_user_flow(hass)
        first_result_task = asyncio.create_task(
            hass.config_entries.flow.async_configure(
                first["flow_id"],
                {
                    CONF_BACKEND: BACKEND_SERIAL_PRO,
                    CONF_DEVICE: "/dev/serial/by-id/dmx-interface-1",
                },
            )
        )
        await first_backend.connect_started.wait()

        second = await _start_user_flow(hass)
        second_result = await hass.config_entries.flow.async_configure(
            second["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: "/dev/serial/by-id/dmx-interface-2",
            },
        )

        first_backend.connect_gate.set()
        first_result = await first_result_task

    assert second_result["type"] is FlowResultType.ABORT
    assert second_result["reason"] == "already_in_progress"
    assert second_backend.connect_calls == 0
    assert first_result["type"] is FlowResultType.CREATE_ENTRY


async def test_unexpected_error_releases_in_progress_identity(
    hass: HomeAssistant,
) -> None:
    """A failed reserved step cannot block a later flow for the same device."""
    backend = FakeBackend()
    comparison_key = "/dev/ttyUSB0"
    comparison_results = [
        comparison_key,
        comparison_key,
        RuntimeError("canonical comparison failed"),
        comparison_key,
        comparison_key,
        comparison_key,
        comparison_key,
    ]
    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(side_effect=[[], []]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(side_effect=lambda _hass, path: path),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_path_comparison_key",
            AsyncMock(side_effect=comparison_results),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
    ):
        first = await _start_user_flow(hass)
        first_manual = await hass.config_entries.flow.async_configure(
            first["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        with pytest.raises(RuntimeError, match="canonical comparison failed"):
            await hass.config_entries.flow.async_configure(
                first_manual["flow_id"],
                {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB0"},
            )

        second = await _start_user_flow(hass)
        second_manual = await hass.config_entries.flow.async_configure(
            second["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        result = await hass.config_entries.flow.async_configure(
            second_manual["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB0"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


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
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(return_value="/dev/serial/by-id/replacement"),
        ) as get_stable_path,
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
                CONF_DEVICE: "/dev//ttyUSB1",
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
    get_stable_path.assert_awaited_once_with(hass, "/dev/ttyUSB1")
    assert backend.disconnect_calls == 1


async def test_reconfigure_rejects_foreign_device_before_opening(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Reconfigure reports a foreign canonical device conflict without opening."""
    current = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/ttyUSB0",
            CONF_INTERFACE_ID: "current-interface",
        },
        unique_id="current-interface",
    )
    current.add_to_hass(hass)
    device = tmp_path / "ttyUSB1"
    device.touch()
    by_id = tmp_path / "serial" / "by-id"
    by_id.mkdir(parents=True)
    stable_path = by_id / "foreign-interface"
    stable_path.symlink_to(device)
    foreign = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: str(stable_path),
            CONF_INTERFACE_ID: "foreign-interface",
        },
        unique_id="foreign-interface",
    )
    foreign.add_to_hass(hass)

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(side_effect=lambda _hass, path: path),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            new=AsyncMock(),
        ) as create_backend,
    ):
        form = await current.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: f"{tmp_path}//ttyUSB1",
            },
        )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "reconfigure"
    assert result["errors"] == {"base": "already_configured"}
    create_backend.assert_not_awaited()


async def test_reconfigure_excludes_current_entry_from_device_duplicates(
    hass: HomeAssistant,
    tmp_path: Path,
) -> None:
    """Reconfigure accepts an alias of its own canonical device path."""
    device = tmp_path / "ttyUSB0"
    device.touch()
    by_id = tmp_path / "serial" / "by-id"
    by_id.mkdir(parents=True)
    stable_path = by_id / "current-interface"
    stable_path.symlink_to(device)
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: str(stable_path),
            CONF_INTERFACE_ID: "current-interface",
        },
        unique_id="current-interface",
    )
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(side_effect=lambda _hass, path: path),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(hass.config_entries, "async_schedule_reload"),
    ):
        form = await entry.start_reconfigure_flow(hass)
        result = await hass.config_entries.flow.async_configure(
            form["flow_id"],
            {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: str(device),
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    assert entry.data[CONF_DEVICE] == str(device)


async def _assert_reconfigure_exception_releases_reservation(
    hass: HomeAssistant, failure: BaseException
) -> None:
    """Exercise one exceptional post-reservation reconfigure exit."""
    entry = MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/ttyUSB0",
            CONF_INTERFACE_ID: "current-interface",
        },
        unique_id="current-interface",
    )
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.config_flow._async_scan_serial_ports",
            AsyncMock(return_value=[]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow._async_get_stable_path",
            AsyncMock(side_effect=lambda _hass, path: path),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.UsbDmxConfigFlow._async_is_device_configured",
            AsyncMock(side_effect=[False, failure, False, False, False]),
        ),
        patch(
            "custom_components.usb_dmx.config_flow.async_create_backend",
            AsyncMock(return_value=backend),
        ),
    ):
        reconfigure = await entry.start_reconfigure_flow(hass)
        with pytest.raises(type(failure)):
            await hass.config_entries.flow.async_configure(
                reconfigure["flow_id"],
                {
                    CONF_BACKEND: BACKEND_SERIAL_PRO,
                    CONF_DEVICE: "/dev/ttyUSB1",
                },
            )

        user = await _start_user_flow(hass)
        manual = await hass.config_entries.flow.async_configure(
            user["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: MANUAL_PATH},
        )
        result = await hass.config_entries.flow.async_configure(
            manual["flow_id"],
            {CONF_BACKEND: BACKEND_SERIAL_PRO, CONF_DEVICE: "/dev/ttyUSB1"},
        )

    assert result["type"] is FlowResultType.CREATE_ENTRY


async def test_reconfigure_cancellation_releases_device_reservation(
    hass: HomeAssistant,
) -> None:
    """Cancellation after reservation cannot block a later manual flow."""
    await _assert_reconfigure_exception_releases_reservation(
        hass, asyncio.CancelledError()
    )


async def test_reconfigure_runtime_error_releases_device_reservation(
    hass: HomeAssistant,
) -> None:
    """A runtime error after reservation cannot block a later manual flow."""
    await _assert_reconfigure_exception_releases_reservation(
        hass, RuntimeError("duplicate check failed")
    )
