"""Central restore with actual HA registry, saved states and extra data."""

from __future__ import annotations

from collections.abc import Callable
from datetime import timedelta
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant.const import Platform
from homeassistant.core import State
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import restore_state as rs
from homeassistant.util import dt as dt_util
from pytest_homeassistant_custom_component.common import (
    mock_restore_cache_with_extra_data,
)

from custom_components.usb_dmx import restore as dimmer_restore
from custom_components.usb_dmx.const import DOMAIN
from custom_components.usb_dmx.models import StartupBehavior

from .fakes import FakeBackend
from .test_dimmer_entities import _ids

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)


@pytest.mark.parametrize(
    "platforms",
    [
        (Platform.NUMBER, Platform.LIGHT, Platform.SELECT),
        (Platform.LIGHT, Platform.SELECT, Platform.NUMBER),
    ],
)
@pytest.mark.parametrize("disabled", [None, "light", "level", "both"])
async def test_restore_once_prefers_legacy_light_with_disabled_companions(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    platforms: tuple[Platform, ...],
    disabled: str | None,
) -> None:
    """Order and disabled companions preserve the old light's primary restore data."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    for domain, suffix, entity_id in (
        ("light", "", "light.front"),
        ("number", "_level", "number.front_level"),
    ):
        registry.async_get_or_create(
            domain,
            DOMAIN,
            f"{entry.entry_id}_{fixture.unique_id}{suffix}",
            suggested_object_id=entity_id.split(".")[1],
            config_entry=entry,
            config_subentry_id=fixture.subentry_id,
            disabled_by=er.RegistryEntryDisabler.USER
            if disabled in ("both", "light" if domain == "light" else "level")
            else None,
        )
    mock_restore_cache_with_extra_data(
        hass,
        [
            (
                State("light.front", "on", {"brightness": 128}),
                {"last_on_brightness": 200},
            ),
            (State("number.front_level", "0.3"), {"last_on_brightness": 76}),
        ],
    )
    backend = FakeBackend()
    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch("custom_components.usb_dmx.PLATFORMS", platforms),
        patch.object(
            dimmer_restore, "_restore_values", wraps=dimmer_restore._restore_values
        ) as resolve,
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        runtime = entry.runtime_data.dimmers[fixture.unique_id]
        assert resolve.call_count == (0 if disabled == "both" else 2)
        if disabled == "both":
            assert backend.frames == [bytes(512)]
            assert runtime.brightness is None
        else:
            assert [frame[0] for frame in backend.frames] == [0, 128]
            assert runtime.brightness == 128
            assert runtime.last_on_brightness == 200
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_disabled_light_fader_reload_uses_newer_exact_state(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A stale disabled Light cannot override the active Fader's newest saved state."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    registry = er.async_get(hass)
    registry.async_get_or_create(
        "light",
        DOMAIN,
        f"{entry.entry_id}_{fixture.unique_id}",
        suggested_object_id="front",
        config_entry=entry,
        config_subentry_id=fixture.subentry_id,
        disabled_by=er.RegistryEntryDisabler.USER,
    )
    registry.async_get_or_create(
        "number",
        DOMAIN,
        f"{entry.entry_id}_{fixture.unique_id}_level",
        suggested_object_id="front_level",
        config_entry=entry,
        config_subentry_id=fixture.subentry_id,
    )
    mock_restore_cache_with_extra_data(
        hass,
        [
            (State("light.front", "on", {"brightness": 128}), {}),
            (
                State("number.front_level", "0.3"),
                {"brightness": 77, "last_on_brightness": 200},
            ),
        ],
    )
    rs.async_get(hass).last_states["light.front"].last_seen -= timedelta(days=1)
    backends = [FakeBackend(), FakeBackend(), FakeBackend()]
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(side_effect=backends),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert entry.runtime_data.dimmers[fixture.unique_id].brightness == 77
        assert [frame[0] for frame in backends[0].frames] == [0, 76]
        ids = _ids(hass, entry, fixture.unique_id)
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["level"], "value": 0.7},
            blocking=True,
        )
        assert await hass.config_entries.async_reload(entry.entry_id)
        assert entry.runtime_data.dimmers[fixture.unique_id].brightness == 178
        assert [frame[0] for frame in backends[1].frames] == [0, 178]
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["level"], "value": 0},
            blocking=True,
        )
        await rs.async_get(hass).async_dump_states()
        assert await hass.config_entries.async_unload(entry.entry_id)
        await rs.async_get(hass).async_load()
        assert await hass.config_entries.async_setup(entry.entry_id)
        runtime = entry.runtime_data.dimmers[fixture.unique_id]
        assert runtime.brightness is None
        assert runtime.last_on_brightness == 178
        await hass.services.async_call(
            "number",
            "set_value",
            {"entity_id": ids["level"], "value": 0.3},
            blocking=True,
        )
        assert runtime.brightness == 76
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("startup", [StartupBehavior.RESTORE, StartupBehavior.ZERO])
async def test_off_extra_target_round_trips_real_ha_restore_storage(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    startup: StartupBehavior,
) -> None:
    """Off has no brightness attribute but extra requested target survives reload."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture, startup_behavior=startup)
    entry.add_to_hass(hass)
    backends = [FakeBackend(), FakeBackend()]
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(side_effect=backends),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        ids = _ids(hass, entry, fixture.unique_id)
        await hass.services.async_call(
            "light",
            "turn_on",
            {"entity_id": ids["light"], "brightness": 200},
            blocking=True,
        )
        await hass.services.async_call(
            "light", "turn_off", {"entity_id": ids["light"]}, blocking=True
        )
        assert "brightness" not in hass.states.get(ids["light"]).attributes
        assert await hass.config_entries.async_reload(entry.entry_id)
        runtime = entry.runtime_data.dimmers[fixture.unique_id]
        assert not runtime.is_on
        assert runtime.last_on_brightness == (
            200 if startup is StartupBehavior.RESTORE else None
        )
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": ids["light"]}, blocking=True
        )
        assert runtime.brightness == (
            200 if startup is StartupBehavior.RESTORE else 255
        )
        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("state", "attributes"),
    [("unavailable", {}), ("unknown", {}), ("on", {"brightness": "broken"})],
)
async def test_invalid_current_preserves_valid_extra_target(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    state: str,
    attributes: dict[str, object],
) -> None:
    """Malformed current state cannot discard separately valid remembered target."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light",
        DOMAIN,
        f"{entry.entry_id}_{fixture.unique_id}",
        suggested_object_id="front",
        config_entry=entry,
        config_subentry_id=fixture.subentry_id,
    )
    mock_restore_cache_with_extra_data(
        hass, [(State("light.front", state, attributes), {"last_on_brightness": 200})]
    )
    backend = FakeBackend()
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        runtime = entry.runtime_data.dimmers[fixture.unique_id]
        assert backend.frames == [bytes(512)]
        assert runtime.brightness is None
        assert runtime.last_on_brightness == 200
        await hass.services.async_call(
            "light", "turn_on", {"entity_id": "light.front"}, blocking=True
        )
        assert runtime.brightness == 200
        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_malformed_stored_extra_shape_is_ignored(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Corrupt JSON extra data loaded by HA cannot break a valid legacy state."""
    fixture = make_fixture_subentry(name="Front")
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    er.async_get(hass).async_get_or_create(
        "light",
        DOMAIN,
        f"{entry.entry_id}_{fixture.unique_id}",
        suggested_object_id="front",
        config_entry=entry,
        config_subentry_id=fixture.subentry_id,
    )
    mock_restore_cache_with_extra_data(hass, [])
    rs.async_get(hass).last_states["light.front"] = rs.StoredState.from_dict(
        {
            "state": State("light.front", "on", {"brightness": 128}).as_dict(),
            "last_seen": dt_util.utcnow(),
            "extra_data": ["broken"],
        }
    )
    backend = FakeBackend()
    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        assert entry.runtime_data.dimmers[fixture.unique_id].brightness == 128
        assert [frame[0] for frame in backend.frames] == [0, 128]
        assert await hass.config_entries.async_unload(entry.entry_id)
