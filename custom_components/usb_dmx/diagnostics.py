"""Diagnostics support for USB DMX."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from homeassistant.components.diagnostics import async_redact_data

from .const import (
    CONF_BACKEND,
    CONF_BLACKOUT_ON_SHUTDOWN,
    CONF_DEVICE,
    DEFAULT_BLACKOUT_ON_SHUTDOWN,
    SUBENTRY_TYPE_FIXTURE,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

    from . import UsbDmxConfigEntry


async def async_get_config_entry_diagnostics(
    _hass: HomeAssistant, entry: UsbDmxConfigEntry
) -> dict[str, Any]:
    """Return JSON-safe diagnostics without interface or frame identifiers."""
    runtime = entry.runtime_data
    controller = runtime.controller
    timestamp = controller.last_successful_frame_timestamp
    diagnostics = {
        "backend": entry.data[CONF_BACKEND],
        "available": controller.available,
        CONF_DEVICE: entry.data.get(CONF_DEVICE),
        "channel_count": runtime.backend.capabilities.channel_count,
        "fixture_count": len(entry.get_subentries_of_type(SUBENTRY_TYPE_FIXTURE)),
        "refresh_mode": runtime.backend.capabilities.refresh_mode,
        "last_successful_frame_timestamp": (
            timestamp.isoformat() if timestamp is not None else None
        ),
        "reconnect_count": controller.reconnect_count,
        "startup_behavior": runtime.startup_behavior.value,
        "blackout_on_shutdown": entry.options.get(
            CONF_BLACKOUT_ON_SHUTDOWN, DEFAULT_BLACKOUT_ON_SHUTDOWN
        ),
    }
    return async_redact_data(diagnostics, {CONF_DEVICE})
