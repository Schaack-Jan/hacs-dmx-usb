"""Real HA integration coverage for native dimmer companion controls."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import EntityCategory, UnitOfTime
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform as ep
from homeassistant.helpers import entity_registry as er
from homeassistant.setup import async_setup_component

from custom_components.usb_dmx import UsbDmxRuntime, light, number, select
from custom_components.usb_dmx.const import DOMAIN
from custom_components.usb_dmx.controller import DmxController
from custom_components.usb_dmx.models import StartupBehavior

from .fakes import FakeBackend

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)


def _ids(
    hass: HomeAssistant, entry: MockConfigEntry, fixture_id: str
) -> dict[str, str]:
    registry = er.async_get(hass)
    identities = {
        "light": ("light", ""),
        "level": ("number", "_level"),
        "fade_time": ("number", "_fade_time"),
        "fade_source": ("select", "_fade_source"),
        "global": ("number", "_global_fade_time"),
    }
    result = {}
    for role, (domain, suffix) in identities.items():
        unique_id = (
            f"{entry.entry_id}{suffix}"
            if role == "global"
            else f"{entry.entry_id}_{fixture_id}{suffix}"
        )
        entity_id = registry.async_get_entity_id(domain, DOMAIN, unique_id)
        assert entity_id is not None, role
        result[role] = entity_id
    return result


async def test_native_companions_ownership_metadata_and_setting_services(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """All roles share one interface and settings services do not write or reload."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        registry = er.async_get(hass)
        device = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)[
            0
        ]
        for role, entity_id in ids.items():
            registered = registry.async_get(entity_id)
            assert registered.device_id == device.id
            assert registered.config_subentry_id == (
                None if role == "global" else fixture.subentry_id
            )
            state = hass.states.get(entity_id)
            assert state is not None
            if role in ("fade_time", "global"):
                assert state.attributes["min"] == 0
                assert state.attributes["max"] == 2
                assert state.attributes["step"] == 0.01
                assert state.attributes["mode"] == "box"
                assert state.attributes["unit_of_measurement"] == UnitOfTime.SECONDS
                assert state.attributes["device_class"] == "duration"
                assert registered.entity_category is EntityCategory.CONFIG
            if role == "level":
                assert state.attributes["max"] == 1
                assert state.attributes["step"] == 0.01
                assert state.attributes["mode"] == "slider"
                assert "unit_of_measurement" not in state.attributes
                assert registered.entity_category is None
            if role == "fade_source":
                assert state.attributes["options"] == ["global", "individual"]
                assert registered.entity_category is EntityCategory.CONFIG
        frames = list(backend.frames)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload:
            for role, value in (("global", 0.2), ("fade_time", 0.1)):
                await hass.services.async_call(
                    "number",
                    "set_value",
                    {"entity_id": ids[role], "value": value},
                    blocking=True,
                )
            await hass.services.async_call(
                "select",
                "select_option",
                {"entity_id": ids["fade_source"], "option": "individual"},
                blocking=True,
            )
            reload.assert_not_called()
        assert backend.frames == frames
        runtime = entry.runtime_data.dimmers[fixture.unique_id]
        assert runtime.fade_time == 0.1
        assert runtime.fade_source.value == "individual"
        assert entry.options["global_fade_time"] == 0.2
        shared = entry.runtime_data
        assert await hass.config_entries.async_unload(entry.entry_id)
        assert runtime._listeners == []
        assert shared.settings._listeners == []
        assert shared.controller._availability_listeners == []


async def test_global_exists_without_fixtures(
    hass: HomeAssistant, make_usb_dmx_entry: Callable[..., MockConfigEntry]
) -> None:
    """The global setting is useful on an empty interface."""
    entry = make_usb_dmx_entry()
    entry.add_to_hass(hass)
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=FakeBackend()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_global_fade_time"
        )
        assert entity_id is not None
        assert hass.states.get(entity_id).state == "0.0"
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(("minimum", "maximum"), [(100, 100), (0, 0)])
async def test_light_fader_live_replacement_and_exact_off(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    minimum: int,
    maximum: int,
) -> None:
    """Both native services expose progress, replacement and exact off consistently."""
    fixture = make_fixture_subentry(name="Front", minimum=minimum, maximum=maximum)
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["global"], "value": 0.2},
            blocking=True,
        )
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ids["light"], "brightness": 255},
            blocking=True,
        )
        await asyncio.sleep(0.07)
        light = hass.states.get(ids["light"])
        level = hass.states.get(ids["level"])
        assert light.state == "on"
        assert 0 < light.attributes["brightness"] < 255
        assert float(level.state) == round(light.attributes["brightness"] / 255, 2)
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["level"], "value": 0.3},
            blocking=True,
        )
        await asyncio.sleep(0.25)
        assert hass.states.get(ids["light"]).attributes["brightness"] == 76
        assert float(hass.states.get(ids["level"]).state) == 0.3
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": ids["light"]}, blocking=True
        )
        assert hass.states.get(ids["light"]).state == "on"
        await asyncio.sleep(0.07)
        assert 0 < hass.states.get(ids["light"]).attributes["brightness"] < 76
        await asyncio.sleep(0.2)
        assert hass.states.get(ids["light"]).state == "off"
        assert "brightness" not in hass.states.get(ids["light"]).attributes
        assert float(hass.states.get(ids["level"]).state) == 0
        assert backend.frames[-1][0] == 0
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ids["light"], "transition": 0},
            blocking=True,
        )
        assert hass.states.get(ids["light"]).attributes["brightness"] == 76
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_scene_uses_native_level_and_configured_fade(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Scene reproduction follows the regular Number service and shared fade."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=FakeBackend()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert await async_setup_component(hass, "scene", {})
        await hass.async_block_till_done()
        ids = _ids(hass, entry, fixture.unique_id)
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["global"], "value": 0.1},
            blocking=True,
        )
        await hass.services.async_call(
            "scene",
            "apply",
            {"entities": {ids["level"]: {"state": "0.5"}}},
            blocking=True,
        )
        await asyncio.sleep(0.04)
        assert 0 < hass.states.get(ids["light"]).attributes["brightness"] < 128
        await asyncio.sleep(0.15)
        assert hass.states.get(ids["light"]).attributes["brightness"] == 128
        assert await hass.config_entries.async_unload(entry.entry_id)


def _entity(hass: HomeAssistant, entity_id: str) -> object:
    """Find the live native entity behind its HA registry identity."""
    return next(
        platform.entities[entity_id]
        for platform in ep.async_get_platforms(hass, DOMAIN)
        if entity_id in platform.entities
    )


@pytest.mark.parametrize(
    ("value", "brightness", "level"),
    [
        (0, None, 0),
        (1, 255, 1),
        (0.3, 76, 0.3),
        (0.5, 128, 0.5),
        (0.503, 128, 0.5),
        (0.002, 1, 0),
        (0.001, None, 0),
    ],
)
async def test_native_fader_conversion_accepts_off_grid_input(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    value: float,
    *,
    brightness: int | None,
    level: float,
) -> None:
    """The native service preserves full input precision until brightness rounding."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=FakeBackend()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["level"], "value": value},
            blocking=True,
        )
        assert entry.runtime_data.dimmers[fixture.unique_id].brightness == brightness
        assert float(hass.states.get(ids["level"]).state) == level
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("role", ["level", "fade_time", "global"])
@pytest.mark.parametrize(
    "value", [True, "0.5", float("nan"), float("inf"), -0.01, 2.01]
)
async def test_number_controls_reject_invalid_direct_calls_without_mutation(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    role: str,
    value: object,
) -> None:
    """Direct API boundaries reject bad values before storage, output or state."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        entity = _entity(hass, ids[role])
        frames = list(backend.frames)
        options = dict(entry.options)
        data = dict(entry.subentries[fixture.subentry_id].data)
        before = hass.states.get(ids[role])
        with pytest.raises((TypeError, ValueError)):
            await entity.async_set_native_value(value)
        assert backend.frames == frames
        assert dict(entry.options) == options
        assert dict(entry.subentries[fixture.subentry_id].data) == data
        assert hass.states.get(ids[role]) is before
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_role_callbacks_deduplicate_progress_and_unrelated_settings(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Configuration roles do not subscribe to brightness ticks or republish peers."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=FakeBackend()),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        dimmer = entry.runtime_data.dimmers[fixture.unique_id]
        assert len(dimmer._listeners) == 2
        for role, name in (
            ("level", "Front Level"),
            ("fade_time", "Front Fade time"),
            ("fade_source", "Front Fade source"),
        ):
            assert hass.states.get(ids[role]).attributes["friendly_name"].endswith(name)
        light, level = _entity(hass, ids["light"]), _entity(hass, ids["level"])
        fade, source, global_fade = (
            _entity(hass, ids[role]) for role in ("fade_time", "fade_source", "global")
        )
        with (
            patch.object(light, "async_write_ha_state") as light_write,
            patch.object(level, "async_write_ha_state") as level_write,
            patch.object(fade, "async_write_ha_state") as fade_write,
            patch.object(source, "async_write_ha_state") as source_write,
            patch.object(global_fade, "async_write_ha_state") as global_write,
        ):
            await dimmer.async_turn_on(100, transition=0.1)
            await asyncio.sleep(0.15)
            assert light_write.call_count >= 2
            assert level_write.call_count >= 2
            fade_write.assert_not_called()
            source_write.assert_not_called()
            global_write.assert_not_called()
            light_count, level_count = light_write.call_count, level_write.call_count
            await dimmer.async_turn_on(100, transition=0)
            assert light_write.call_count == light_count
            assert level_write.call_count == level_count
            await dimmer.async_set_fade_time(0.5)
            await dimmer.async_set_fade_time(0.5)
            fade_write.assert_called_once()
            source_write.assert_not_called()
            global_write.assert_not_called()
            await dimmer.async_set_fade_source("individual")
            source_write.assert_called_once()
            await entry.runtime_data.settings.async_set_global_fade_time(0.5)
            global_write.assert_called_once()
            assert light_write.call_count == light_count
            assert level_write.call_count == level_count
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_native_settings_persist_override_and_source_on_reload(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Persist global time and individual zero across source changes and reload."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(side_effect=[FakeBackend(), FakeBackend()]),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["global"], "value": 0.15},
            blocking=True,
        )
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": ids["fade_source"], "option": "individual"},
            blocking=True,
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["level"], "value": 1},
            blocking=True,
        )
        assert entry.runtime_data.dimmers[fixture.unique_id].brightness == 255
        assert entry.runtime_data.controller._transition_tasks == {}
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert _ids(hass, entry, fixture.unique_id) == ids
        assert float(hass.states.get(ids["global"]).state) == 0.15
        assert float(hass.states.get(ids["fade_time"]).state) == 0
        assert hass.states.get(ids["fade_source"]).state == "individual"
        await hass.services.async_call(
            "select",
            "select_option",
            {"entity_id": ids["fade_source"], "option": "global"},
            blocking=True,
        )
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["level"], "value": 1},
            blocking=True,
        )
        await asyncio.sleep(0.04)
        assert 0 < entry.runtime_data.dimmers[fixture.unique_id].brightness < 255
        await asyncio.sleep(0.2)
        assert entry.runtime_data.dimmers[fixture.unique_id].brightness == 255
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("first_platform", ["light", "select", "number"])
async def test_manually_constructed_runtime_initializes_common_settings(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    first_platform: str,
) -> None:
    """Manual runtime factories use the same state helper irrespective of order."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    entry.runtime_data = UsbDmxRuntime(
        DmxController(backend), backend, StartupBehavior.ZERO
    )
    added = []

    def add(entities: list[object], *, config_subentry_id: str | None) -> None:
        assert config_subentry_id in (None, fixture.subentry_id)
        added.extend(entities)

    for platform in [
        first_platform,
        *[
            platform
            for platform in ("light", "number", "select")
            if platform != first_platform
        ],
    ]:
        await {"light": light, "number": number, "select": select}[
            platform
        ].async_setup_entry(hass, entry, add)
    fade = next(
        entity
        for entity in added
        if entity.unique_id.endswith("_fade_time")
        and not entity.unique_id.endswith("_global_fade_time")
    )
    await fade.async_set_native_value(0.5)
    runtime = entry.runtime_data.dimmers[fixture.unique_id]
    assert runtime.settings is entry.runtime_data.settings
    assert runtime.fade_time == 0.5
    await runtime.async_remove(blackout=False)
    assert backend.frames == []


async def test_offline_output_availability_and_live_reconnect(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Both output roles become unavailable while settings and logical fade continue."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    backend.reconnect_gate = asyncio.Event()
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        runtime = entry.runtime_data
        runtime.controller._reconnect_delays = (0,)
        backend.send_failures = 1
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ids["light"], "brightness": 200, "transition": 0.1},
            blocking=True,
        )
        await backend.reconnect_started.wait()
        assert hass.states.get(ids["light"]).state == "unavailable"
        assert hass.states.get(ids["level"]).state == "unavailable"
        for role in ("fade_time", "global", "fade_source"):
            assert hass.states.get(ids[role]).state != "unavailable"
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["global"], "value": 2},
            blocking=True,
        )
        await asyncio.sleep(0.15)
        assert runtime.dimmers[fixture.unique_id].brightness == 200
        backend.reconnect_gate.set()
        async with asyncio.timeout(1):
            while hass.states.get(ids["light"]).state == "unavailable":  # noqa: ASYNC110
                await asyncio.sleep(0)
        assert hass.states.get(ids["light"]).attributes["brightness"] == 200
        assert float(hass.states.get(ids["level"]).state) == 0.78
        assert backend.frames[-1] == runtime.controller.current_frame
        assert await hass.config_entries.async_unload(entry.entry_id)
