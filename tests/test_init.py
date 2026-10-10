"""Tests for USB DMX config-entry lifecycle."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import (
    SIGNAL_CONFIG_ENTRY_CHANGED,
    ConfigEntryNotReady,
)
from homeassistant.const import Platform
from homeassistant.exceptions import ConfigEntryError
from homeassistant.helpers.dispatcher import DATA_DISPATCHER
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
    from homeassistant.config_entries import ConfigSubentry
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
        assert platforms == (Platform.LIGHT, Platform.NUMBER, Platform.SELECT)

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

    unload_mock.assert_awaited_once_with(
        entry, (Platform.LIGHT, Platform.NUMBER, Platform.SELECT)
    )
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


async def test_setup_initializes_settings_and_dimmers_before_forwarding(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """All output runtimes exist before platform order can affect restore."""
    dimmer = make_fixture_subentry(address=1)
    raw = make_fixture_subentry(fixture_type="raw", address=2, name="Raw")
    entry = make_usb_dmx_entry(dimmer, raw)
    backend = FakeBackend()

    async def forward(
        forwarded_entry: MockConfigEntry, _platforms: tuple[Platform, ...]
    ) -> None:
        runtime = forwarded_entry.runtime_data
        assert runtime.settings.global_fade_time == 0
        assert set(runtime.dimmers) == {dimmer.unique_id}
        assert runtime.dimmers[dimmer.unique_id].settings is runtime.settings
        assert not runtime.dimmers[dimmer.unique_id].is_on
        assert backend.frames == [bytes(512)]

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=forward
        ),
    ):
        assert await async_setup_entry(hass, entry)
    await entry.runtime_data.controller.async_stop()


@pytest.mark.parametrize(
    ("key", "value"), [("global_fade_time", True), ("global_fade_time", float("nan"))]
)
async def test_setup_rejects_bad_settings_before_opening_hardware(
    hass: HomeAssistant, key: str, value: object
) -> None:
    """Malformed persisted fade configuration becomes a controlled setup error."""
    entry = _entry()
    entry.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        entry, options=dict(entry.options) | {key: value}
    )
    with patch(
        "custom_components.usb_dmx.async_create_backend", new=AsyncMock()
    ) as create:
        with pytest.raises(ConfigEntryError, match="invalid_fade_time"):
            await async_setup_entry(hass, entry)
        create.assert_not_called()


async def test_setup_rejects_bad_fixture_settings(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Every fixture is validated before startup even if its entities are disabled."""
    subentry = make_fixture_subentry()
    subentry = replace(subentry, data=dict(subentry.data) | {"fade_source": "broken"})
    entry = make_usb_dmx_entry(subentry)
    with patch(
        "custom_components.usb_dmx.async_create_backend", new=AsyncMock()
    ) as create:
        with pytest.raises(ConfigEntryError, match="invalid_fade_source"):
            await async_setup_entry(hass, entry)
        create.assert_not_called()


async def test_fixture_bearing_forward_failure_releases_subscriptions(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Platform failure cannot leak runtime settings or deletion observation."""
    entry = make_usb_dmx_entry(make_fixture_subentry())
    entry.add_to_hass(hass)
    captured = []
    listeners_before = set(
        hass.data.get(DATA_DISPATCHER, {}).get(SIGNAL_CONFIG_ENTRY_CHANGED, {})
    )

    async def forward(
        forwarded: MockConfigEntry, _platforms: tuple[Platform, ...]
    ) -> None:
        captured.append(forwarded.runtime_data)
        assert len(forwarded.runtime_data.settings._listeners) == 1
        message = "platform failed"
        raise RuntimeError(message)

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=FakeBackend()),
        ),
        patch.object(
            hass.config_entries, "async_forward_entry_setups", side_effect=forward
        ),
        pytest.raises(RuntimeError, match="platform failed"),
    ):
        await async_setup_entry(hass, entry)
    shared = captured[0]
    assert shared.settings._listeners == []
    assert shared.controller._availability_listeners == []
    assert shared.controller._transition_tasks == {}
    assert all(
        dimmer._unsubscribe_settings is None and dimmer._listeners == []
        for dimmer in shared.dimmers.values()
    )
    assert (
        set(hass.data.get(DATA_DISPATCHER, {}).get(SIGNAL_CONFIG_ENTRY_CHANGED, {}))
        == listeners_before
    )
