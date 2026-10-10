"""HA lifecycle, queued deletion and fixture reconfiguration acceptance tests."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.config_entries import SIGNAL_CONFIG_ENTRY_CHANGED, ConfigEntryState
from homeassistant.const import Platform
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform as ep
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state as rs
from homeassistant.helpers.dispatcher import DATA_DISPATCHER

from custom_components.usb_dmx import number as number_platform
from custom_components.usb_dmx import select as select_platform
from custom_components.usb_dmx.const import DOMAIN
from custom_components.usb_dmx.models import StartupBehavior

from .fakes import FakeBackend
from .test_dimmer_entities import _ids

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback
    from pytest_homeassistant_custom_component.common import MockConfigEntry

    from custom_components.usb_dmx import UsbDmxRuntime

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)


async def test_loaded_options_flow_preserves_automatic_reload(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Deletion observation must not conflict with HA's OptionsFlowWithReload."""
    entry = make_usb_dmx_entry(make_fixture_subentry())
    entry.add_to_hass(hass)
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=FakeBackend()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        form = await hass.config_entries.options.async_init(entry.entry_id)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            result = await hass.config_entries.options.async_configure(
                form["flow_id"],
                {"startup_behavior": "zero", "blackout_on_shutdown": True},
            )
            assert result["type"] is FlowResultType.CREATE_ENTRY
            reload.assert_called_once_with(entry.entry_id)
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_queued_companion_writes_are_terminal_on_delete(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Removing a dimmer defeats light, fader and settings queued at its lock."""
    fixture = make_fixture_subentry(name="Front")
    sibling = make_fixture_subentry(name="Relay", fixture_type="raw", address=20)
    entry = make_usb_dmx_entry(fixture, sibling, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        shared = entry.runtime_data
        runtime = shared.dimmers[fixture.unique_id]
        registry = er.async_get(hass)
        raw_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_{sibling.unique_id}"
        )
        device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[
            0
        ]
        await hass.services.async_call(
            "number", "set_value", {"entity_id": raw_id, "value": 200}, blocking=True
        )
        backend.send_gate = asyncio.Event()
        backend.send_started.clear()
        active = asyncio.create_task(
            hass.services.async_call(
                "light",
                "turn_on",
                {"entity_id": ids["light"], "brightness": 64, "transition": 0},
                blocking=True,
            )
        )
        await backend.send_started.wait()
        queued = [
            asyncio.create_task(
                hass.services.async_call(
                    domain, service, {"entity_id": ids[role], key: value}, blocking=True
                )
            )
            for domain, service, role, key, value in (
                ("number", "set_value", "level", "value", 1),
                ("number", "set_value", "fade_time", "value", 2),
                ("select", "select_option", "fade_source", "option", "individual"),
            )
        ]
        await asyncio.sleep(0)
        assert hass.config_entries.async_remove_subentry(entry, fixture.subentry_id)
        try:
            async with asyncio.timeout(1):
                while not runtime._removing:  # noqa: ASYNC110
                    await asyncio.sleep(0)
        finally:
            backend.send_gate.set()
        await asyncio.gather(active, *queued)
        await hass.async_block_till_done()
        assert shared.controller.current_frame[0] == 0
        assert backend.frames[-1][0] == 0
        assert shared.controller.current_frame[19] == 200
        assert shared.controller._transition_tasks == {}
        for role in ("light", "level", "fade_time", "fade_source"):
            assert registry.async_get(ids[role]) is None
            assert hass.states.get(ids[role]) is None
        assert hass.states.get(raw_id).state == "200"
        assert dr.async_get(hass).async_get(device.id) is not None
        assert fixture.subentry_id not in entry.subentries
        assert runtime._listeners == []
        assert runtime._unsubscribe_settings is None
        assert len(shared.settings._listeners) == 1
        assert len(shared.controller._availability_listeners) == 1
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert shared.settings._listeners == []


async def test_all_disabled_deleted_fixture_still_releases_runtime(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A missing live entity cannot prevent central terminal runtime cleanup."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, suffix in (
        ("light", ""),
        ("number", "_level"),
        ("number", "_fade_time"),
        ("select", "_fade_source"),
    ):
        registry.async_get_or_create(
            domain,
            DOMAIN,
            f"{entry.entry_id}_{fixture.unique_id}{suffix}",
            config_entry=entry,
            config_subentry_id=fixture.subentry_id,
            disabled_by=er.RegistryEntryDisabler.USER,
        )
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=FakeBackend()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        runtime = entry.runtime_data.dimmers[fixture.unique_id]
        assert runtime._unsubscribe_settings is not None
        assert hass.config_entries.async_remove_subentry(entry, fixture.subentry_id)
        await hass.async_block_till_done()
        assert runtime._removed
        assert runtime._unsubscribe_settings is None
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("fixture_type", "address", "minimum", "maximum"),
    [("raw", 2, 0, 255), ("dimmer", 2, 0, 255), ("dimmer", 1, 50, 150)],
)
async def test_reconfigure_cleans_obsolete_registry_and_old_transitions(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    fixture_type: str,
    *,
    address: int,
    minimum: int,
    maximum: int,
) -> None:
    """Real reconfigure retains identity, siblings and device but retires old roles."""
    fixture = make_fixture_subentry(name="Front")
    sibling = make_fixture_subentry(name="Relay", fixture_type="raw", address=20)
    entry = make_usb_dmx_entry(fixture, sibling, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backends = [FakeBackend(), FakeBackend()]
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(side_effect=backends),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        old = entry.runtime_data
        registry = er.async_get(hass)
        device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[
            0
        ]
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ids["light"], "brightness": 255, "transition": 60},
            blocking=True,
        )
        assert old.controller._transition_tasks
        form = await entry.start_subentry_reconfigure_flow(hass, fixture.subentry_id)
        result = await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            {
                "type": fixture_type,
                "name": "Front",
                "address": address,
                "minimum": minimum,
                "maximum": maximum,
            },
        )
        assert result["reason"] == "reconfigure_successful"
        await hass.async_block_till_done()
        assert entry.runtime_data is not old
        assert old.controller._transition_tasks == {}
        assert old.controller._availability_listeners == []
        assert old.settings._listeners == []
        assert [frame[address - 1] for frame in backends[1].frames] == [0]
        if fixture_type == "raw":
            for role in ("light", "level", "fade_time", "fade_source"):
                assert registry.async_get(ids[role]) is None
                assert hass.states.get(ids[role]) is None
            raw_id = registry.async_get_entity_id(
                "number", DOMAIN, f"{entry.entry_id}_{fixture.unique_id}"
            )
            assert raw_id is not None
        else:
            assert _ids(hass, entry, fixture.unique_id) == ids
            assert (
                entry.runtime_data.dimmers[fixture.unique_id].fixture.address == address
            )
        assert dr.async_get(hass).async_get(device.id) is not None
        assert sibling.subentry_id in entry.subentries
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_partially_loaded_platform_failure_removes_entity_subscriptions(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A real loaded output platform is unwound when later forwarding fails."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    forward_platforms = hass.config_entries.async_forward_entry_setups
    captured = []

    async def fail_after_light(
        forwarded: MockConfigEntry, _platforms: tuple[Platform, ...]
    ) -> None:
        await forward_platforms(forwarded, (Platform.LIGHT,))
        shared = forwarded.runtime_data
        captured.append(shared)
        assert len(shared.controller._availability_listeners) == 1
        assert len(shared.dimmers[fixture.unique_id]._listeners) == 1
        message = "later platform failed"
        raise RuntimeError(message)

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=FakeBackend()),
        ),
        patch.object(
            hass.config_entries,
            "async_forward_entry_setups",
            side_effect=fail_after_light,
        ),
    ):
        assert not await hass.config_entries.async_setup(entry.entry_id)
    shared = captured[0]
    assert shared.settings._listeners == []
    assert shared.dimmers[fixture.unique_id]._listeners == []
    assert shared.controller._availability_listeners == []
    assert shared.controller._transition_tasks == {}
    assert not hasattr(entry, "runtime_data")
    # The component is ready for a clean retry, including the previously loaded role.
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=FakeBackend()),
    ):
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert _ids(hass, entry, fixture.unique_id)
        assert await hass.config_entries.async_unload(entry.entry_id)


def _assert_failed_setup_cleanup(
    hass: HomeAssistant,
    entry: MockConfigEntry,
    shared: UsbDmxRuntime,
    restore_before: set[str],
    deletion_before: set[Callable[..., None]],
) -> None:
    """Check every entry-owned subscription after an actual platform failure."""
    assert not hasattr(entry, "runtime_data")
    assert shared.settings._listeners == []
    assert shared.controller._availability_listeners == []
    assert shared.controller._transition_tasks == {}
    assert all(
        dimmer._listeners == [] and dimmer._unsubscribe_settings is None
        for dimmer in shared.dimmers.values()
    )
    assert set(rs.async_get(hass).entities) == restore_before
    assert (
        set(hass.data.get(DATA_DISPATCHER, {}).get(SIGNAL_CONFIG_ENTRY_CHANGED, {}))
        == deletion_before
    )
    assert all(
        not platform.entities
        for platform in ep.async_get_platforms(hass, DOMAIN)
        if platform.config_entry is entry
    )


@pytest.mark.parametrize("failed_domain", [Platform.NUMBER, Platform.SELECT])
async def test_real_platform_setup_failure_rolls_back_and_preserves_other_interface(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    failed_domain: Platform,
) -> None:
    """An exception swallowed inside HA platform setup still fails the parent."""
    fixture, other_fixture = (
        make_fixture_subentry(name="Front"),
        make_fixture_subentry(name="Back"),
    )
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    other = make_usb_dmx_entry(
        other_fixture, startup_behavior=StartupBehavior.ZERO, title="Other Interface"
    )
    other.add_to_hass(hass)
    hass.config_entries.async_update_entry(
        other,
        unique_id="dmx-other",
        data=dict(other.data)
        | {"device": "/dev/serial/by-id/dmx-other", "interface_id": "dmx-other"},
    )
    failed_backend, other_backend, retry_backend = (
        FakeBackend(),
        FakeBackend(),
        FakeBackend(),
    )
    captured = []
    module = {Platform.NUMBER: number_platform, Platform.SELECT: select_platform}[
        failed_domain
    ]
    original_setup = module.async_setup_entry

    async def fail_inside_platform(
        platform_hass: HomeAssistant,
        forwarded: MockConfigEntry,
        add_entities: AddConfigEntryEntitiesCallback,
    ) -> None:
        await original_setup(platform_hass, forwarded, add_entities)
        if forwarded is not entry:
            return
        platform = ep.current_platform.get()
        # Finish actual entity addition, including RestoreEntity registration,
        # before failing inside HA's platform setup exception boundary.
        await asyncio.gather(*platform._tasks)
        shared = forwarded.runtime_data
        captured.append(shared)
        assert platform.entities
        message = "failure inside actual platform setup"
        raise RuntimeError(message)

    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(side_effect=[other_backend, failed_backend, retry_backend]),
    ):
        assert await hass.config_entries.async_setup(other.entry_id)
        entry.add_to_hass(hass)
        other_runtime = other.runtime_data
        other_ids = _ids(hass, other, other_fixture.unique_id)
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": other_ids["light"], "brightness": 76},
            blocking=True,
        )
        other_frames, other_availability, other_settings = (
            list(other_backend.frames),
            list(other_runtime.controller._availability_listeners),
            list(other_runtime.settings._listeners),
        )
        restore_before = set(rs.async_get(hass).entities)
        deletion_before = set(
            hass.data.get(DATA_DISPATCHER, {}).get(SIGNAL_CONFIG_ENTRY_CHANGED, {})
        )
        with patch.object(
            module, "async_setup_entry", side_effect=fail_inside_platform
        ):
            assert not await hass.config_entries.async_setup(entry.entry_id)
        assert entry.state is ConfigEntryState.SETUP_RETRY
        _assert_failed_setup_cleanup(
            hass, entry, captured[0], restore_before, deletion_before
        )
        assert not failed_backend.connected
        assert failed_backend.disconnect_calls == 1
        assert other.state is ConfigEntryState.LOADED
        assert other.runtime_data is other_runtime
        assert other_backend.connected
        assert other_backend.frames == other_frames
        assert other_runtime.controller._availability_listeners == other_availability
        assert other_runtime.settings._listeners == other_settings
        assert hass.states.get(other_ids["light"]).attributes["brightness"] == 76
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert entry.state is ConfigEntryState.LOADED
        assert _ids(hass, entry, fixture.unique_id)
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert other_backend.connected
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": other_ids["level"], "value": 0.5},
            blocking=True,
        )
        assert other_runtime.dimmers[other_fixture.unique_id].brightness == 128
        assert await hass.config_entries.async_unload(other.entry_id)
