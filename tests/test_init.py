"""Tests for USB DMX config-entry lifecycle."""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import ConfigEntryNotReady
from homeassistant.const import Platform
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.usb_dmx import (
    UsbDmxRuntime,
    async_setup_entry,
    async_unload_entry,
)
from custom_components.usb_dmx.const import (
    BACKEND_SERIAL_PRO,
    CONF_BACKEND,
    CONF_BLACKOUT_ON_SHUTDOWN,
    CONF_DEVICE,
    CONF_INTERFACE_ID,
    CONF_STARTUP_BEHAVIOR,
    DOMAIN,
)
from custom_components.usb_dmx.models import StartupBehavior

from .fakes import FakeBackend

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant


def _entry(
    *,
    blackout: bool | None = False,
    startup_behavior: StartupBehavior | None = None,
) -> MockConfigEntry:
    """Build a representative USB DMX config entry."""
    options = {}
    if blackout is not None:
        options[CONF_BLACKOUT_ON_SHUTDOWN] = blackout
    if startup_behavior is not None:
        options[CONF_STARTUP_BEHAVIOR] = startup_behavior.value
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/serial/by-id/dmx-test",
            CONF_INTERFACE_ID: "dmx-test",
        },
        options=options,
        unique_id="dmx-test",
    )


async def test_setup_connects_before_forwarding_and_stores_runtime(
    hass: HomeAssistant,
) -> None:
    """Setup connects one typed runtime before forwarding both platforms."""
    entry = _entry()
    backend = FakeBackend()

    async def forward(
        forwarded_entry: MockConfigEntry, platforms: tuple[Platform, ...]
    ) -> None:
        assert backend.connected
        assert backend.frames == [bytes(512)]
        assert forwarded_entry.runtime_data.backend is backend
        assert platforms == (Platform.LIGHT, Platform.NUMBER)

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=forward,
        ) as forward_mock,
    ):
        assert await async_setup_entry(hass, entry)

    assert isinstance(entry.runtime_data, UsbDmxRuntime)
    assert entry.runtime_data.backend is backend
    assert entry.runtime_data.controller.available
    forward_mock.assert_awaited_once()


async def test_missing_startup_option_defaults_runtime_to_restore(
    hass: HomeAssistant,
) -> None:
    """A legacy entry without startup options exposes RESTORE to Task 5 entities."""
    entry = _entry(blackout=None)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        await async_setup_entry(hass, entry)

    assert entry.runtime_data.startup_behavior is StartupBehavior.RESTORE
    assert backend.frames == [bytes(512)]


async def test_configured_zero_startup_is_stored_in_runtime(
    hass: HomeAssistant,
) -> None:
    """The ZERO option reaches runtime without mutating controller state."""
    entry = _entry(startup_behavior=StartupBehavior.ZERO)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        await async_setup_entry(hass, entry)

    assert entry.runtime_data.startup_behavior is StartupBehavior.ZERO
    assert entry.runtime_data.controller.current_frame == bytes(512)
    assert backend.frames == [bytes(512)]


async def test_initial_connection_failure_is_not_ready_and_cleans_up(
    hass: HomeAssistant,
) -> None:
    """Expected initial backend failure raises retryable setup and closes transport."""
    entry = _entry()
    backend = FakeBackend()
    backend.connect_failure_after_open = True
    backend.connect_failures = 1

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ) as forward_mock,
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    assert backend.disconnect_calls == 1
    assert not backend.connected
    assert not hasattr(entry, "runtime_data")
    assert backend.frames == []
    forward_mock.assert_not_awaited()


async def test_initial_zero_frame_failure_is_not_ready_and_never_forwards(
    hass: HomeAssistant,
) -> None:
    """Setup cannot expose entities before a physical zero frame succeeds."""
    entry = _entry()
    backend = FakeBackend()
    backend.send_failures = 1

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ) as forward_mock,
        pytest.raises(ConfigEntryNotReady),
    ):
        await async_setup_entry(hass, entry)

    assert backend.send_attempts == 1
    assert backend.frames == []
    assert not backend.connected
    assert not hasattr(entry, "runtime_data")
    forward_mock.assert_not_awaited()


async def test_platform_forward_failure_cleans_up_partial_runtime(
    hass: HomeAssistant,
) -> None:
    """A later setup error cannot leak a connected writer or runtime."""
    entry = _entry()
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            AsyncMock(side_effect=RuntimeError("platform failed")),
        ),
        pytest.raises(RuntimeError, match="platform failed"),
    ):
        await async_setup_entry(hass, entry)

    assert backend.disconnect_calls == 1
    assert not backend.connected
    assert not hasattr(entry, "runtime_data")
    assert backend.frames == [bytes(512)]


@pytest.mark.parametrize("blackout", [False, True])
async def test_successful_unload_stops_transport_and_honors_blackout(
    hass: HomeAssistant, *, blackout: bool
) -> None:
    """Successful unload closes the backend and blacks out only when configured."""
    entry = _entry(blackout=blackout)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        await async_setup_entry(hass, entry)

    await entry.runtime_data.controller.async_set_channel(1, 123)
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=True),
    ) as unload_mock:
        assert await async_unload_entry(hass, entry)

    unload_mock.assert_awaited_once_with(entry, (Platform.LIGHT, Platform.NUMBER))
    assert backend.disconnect_calls == 1
    assert not backend.connected
    assert entry.runtime_data.controller.available is False
    assert (backend.frames[-1] == bytes(512)) is blackout


async def test_platform_unload_failure_keeps_runtime_connected(
    hass: HomeAssistant,
) -> None:
    """Failed platform unload leaves the live runtime untouched for retry."""
    entry = _entry(blackout=True)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        await async_setup_entry(hass, entry)

    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=False),
    ):
        assert not await async_unload_entry(hass, entry)

    assert backend.disconnect_calls == 0
    assert backend.frames == [bytes(512)]
    assert entry.runtime_data.controller.available


async def test_missing_options_default_to_no_shutdown_blackout(
    hass: HomeAssistant,
) -> None:
    """An entry created before options exist closes without sending blackout."""
    entry = _entry(blackout=None)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", new=AsyncMock()
        ),
    ):
        await async_setup_entry(hass, entry)

    await entry.runtime_data.controller.async_set_channel(1, 123)
    with patch.object(
        hass.config_entries,
        "async_unload_platforms",
        AsyncMock(return_value=True),
    ):
        assert await async_unload_entry(hass, entry)

    assert backend.frames == [bytes(512), bytes((123,)) + bytes(511)]
    assert backend.disconnect_calls == 1
