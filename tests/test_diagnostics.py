"""Tests for USB DMX config-entry diagnostics."""

from __future__ import annotations

import json
from collections.abc import Callable, Mapping, Sequence
from typing import TYPE_CHECKING

from homeassistant.components.diagnostics import REDACTED

from custom_components.usb_dmx import UsbDmxRuntime
from custom_components.usb_dmx.controller import DmxController
from custom_components.usb_dmx.diagnostics import async_get_config_entry_diagnostics
from custom_components.usb_dmx.models import StartupBehavior

from .fakes import FakeBackend

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


def _nested_values(value: object) -> list[object]:
    """Flatten diagnostic values so forbidden material can be checked recursively."""
    if isinstance(value, Mapping):
        return [item for nested in value.values() for item in _nested_values(nested)]
    if isinstance(value, Sequence) and not isinstance(value, str | bytes):
        return [item for nested in value for item in _nested_values(nested)]
    return [value]


async def test_diagnostics_are_json_safe_and_redact_identifiers(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A diagnostic download must describe runtime health without identity or frames."""
    fixtures = (
        make_fixture_subentry(name="Front", address=1),
        make_fixture_subentry(name="Back", address=512),
    )
    entry = make_usb_dmx_entry(*fixtures)
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry,
        data={
            **entry.data,
            "device": "/dev/serial/by-id/usb-secret-product-serial-987654",
            "interface_id": "maker:product:raw-unique-id-123456",
        },
    )
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    await controller.async_set_channel(1, 83)
    entry.runtime_data = UsbDmxRuntime(
        controller=controller,
        backend=backend,
        startup_behavior=StartupBehavior.RESTORE,
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics == {
        "backend": "serial_pro",
        "available": True,
        "device": REDACTED,
        "channel_count": 512,
        "fixture_count": 2,
        "refresh_mode": "hardware",
        "last_successful_frame_timestamp": (
            controller.last_successful_frame_timestamp.isoformat()
        ),
        "reconnect_count": 0,
        "startup_behavior": "restore",
        "blackout_on_shutdown": False,
    }
    json.dumps(diagnostics)

    nested_values = _nested_values(diagnostics)
    assert controller.current_frame not in nested_values
    assert bytes([83, *([0] * 511)]) not in nested_values
    assert "/dev/serial/by-id/usb-secret-product-serial-987654" not in nested_values
    assert "maker:product:raw-unique-id-123456" not in nested_values
    assert entry.unique_id not in nested_values

    await controller.async_stop()


async def test_diagnostics_keep_missing_timestamp_deterministic(
    hass: HomeAssistant,
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A runtime with no successful frame must expose JSON null, not an object."""
    entry = make_usb_dmx_entry(startup_behavior=StartupBehavior.ZERO)
    backend = FakeBackend()
    controller = DmxController(backend)
    entry.runtime_data = UsbDmxRuntime(
        controller=controller,
        backend=backend,
        startup_behavior=StartupBehavior.ZERO,
    )

    diagnostics = await async_get_config_entry_diagnostics(hass, entry)

    assert diagnostics["last_successful_frame_timestamp"] is None
    assert diagnostics["available"] is False
    assert diagnostics["fixture_count"] == 0
    assert diagnostics["startup_behavior"] == "zero"
    json.dumps(diagnostics)
