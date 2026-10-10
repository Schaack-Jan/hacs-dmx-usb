"""Tests for persistent interface and fixture fade settings."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest
from homeassistant.config_entries import ConfigSubentry, UnknownSubEntry

from custom_components.usb_dmx.models import FixtureValidationError
from custom_components.usb_dmx.settings import FadeSettings

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry


async def test_settings_defaults_persist_and_notify_after_storage(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Settings write only storage, notify subscribers, and survive new instances."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    settings = FadeSettings(hass, entry)
    assert settings.global_fade_time == 0.0
    assert settings.fixture_settings(fixture.unique_id) == (0.0, "global")
    observations = []
    unsubscribe = settings.add_listener(
        lambda: observations.append(
            (settings.global_fade_time, settings.fixture_settings(fixture.unique_id))
        )
    )
    with patch.object(hass.config_entries, "async_schedule_reload") as reload_mock:
        await settings.async_set_global_fade_time(0.015)
        await settings.async_set_fixture_fade_time(fixture.unique_id, 2)
        await settings.async_set_fixture_fade_source(fixture.unique_id, "individual")
        await hass.async_block_till_done()
        reload_mock.assert_not_called()
    assert observations == [
        (0.01, (0.0, "global")),
        (0.01, (2.0, "global")),
        (0.01, (2.0, "individual")),
    ]
    restored = FadeSettings(hass, entry)
    assert restored.global_fade_time == 0.01
    assert restored.fixture_settings(fixture.unique_id) == (2.0, "individual")
    unsubscribe()
    await settings.async_set_global_fade_time(0)
    assert len(observations) == 3


async def test_settings_concurrent_merges_and_interface_isolation(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Concurrent setters preserve each other's fields and unrelated interfaces."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture)
    other_entry = make_usb_dmx_entry()
    entry.add_to_hass(hass)
    other_entry.add_to_hass(hass)
    current = entry.subentries[fixture.subentry_id]
    hass.config_entries.async_update_subentry(
        entry, current, data=dict(current.data) | {"future": "fixture"}
    )
    hass.config_entries.async_update_entry(
        entry, options=dict(entry.options) | {"future": "interface"}
    )
    settings = FadeSettings(hass, entry)
    other = FadeSettings(hass, other_entry)
    await asyncio.gather(
        settings.async_set_global_fade_time(1.5),
        settings.async_set_fixture_fade_time(fixture.unique_id, 0),
        settings.async_set_fixture_fade_source(fixture.unique_id, "individual"),
    )
    assert settings.fixture_settings(fixture.unique_id) == (0.0, "individual")
    assert settings.global_fade_time == 1.5
    assert other.global_fade_time == 0.0
    assert entry.options["future"] == "interface"
    assert current.data["future"] == "fixture"
    assert current.data["id"] == current.unique_id == fixture.unique_id
    await settings.async_set_fixture_fade_time(fixture.unique_id, 1.2)
    await settings.async_set_fixture_fade_source(fixture.unique_id, "global")
    await settings.async_set_global_fade_time(2)
    assert settings.fixture_settings(fixture.unique_id) == (1.2, "global")
    await settings.async_set_fixture_fade_source(fixture.unique_id, "individual")
    assert settings.fixture_settings(fixture.unique_id) == (1.2, "individual")


@pytest.mark.parametrize(
    "value",
    [True, False, "1", None, float("nan"), float("inf"), float("-inf"), -0.01, 2.01],
)
async def test_settings_reject_invalid_times_without_mutation(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    value: object,
) -> None:
    """Both setter boundaries reject invalid numeric values before publishing."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    settings = FadeSettings(hass, entry)
    options, data = (
        dict(entry.options),
        dict(entry.subentries[fixture.subentry_id].data),
    )
    notifications = []
    settings.add_listener(lambda: notifications.append(True))
    for setter in (
        settings.async_set_global_fade_time,
        lambda value: settings.async_set_fixture_fade_time(fixture.unique_id, value),
    ):
        with pytest.raises(FixtureValidationError, match=r"^invalid_fade_time$"):
            await setter(value)
    assert dict(entry.options) == options
    assert dict(entry.subentries[fixture.subentry_id].data) == data
    assert notifications == []


@pytest.mark.parametrize("value", [True, None, 0, "Global", "individual ", "other"])
async def test_settings_reject_invalid_sources(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    value: object,
) -> None:
    """Source setters preserve storage when the exact source is invalid."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    settings = FadeSettings(hass, entry)
    before = dict(entry.subentries[fixture.subentry_id].data)
    with pytest.raises(FixtureValidationError, match=r"^invalid_fade_source$"):
        await settings.async_set_fixture_fade_source(fixture.unique_id, value)
    assert dict(entry.subentries[fixture.subentry_id].data) == before


@pytest.mark.parametrize(
    ("method", "value"),
    [
        ("async_set_fixture_fade_time", 1),
        ("async_set_fixture_fade_source", "individual"),
    ],
)
async def test_queued_settings_do_not_recreate_deleted_fixture(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    method: str,
    value: object,
) -> None:
    """A setter waiting for its lock rechecks deletion before updating storage."""
    fixture = make_fixture_subentry()
    sibling = make_fixture_subentry(name="Sibling", address=2)
    entry = make_usb_dmx_entry(fixture, sibling)
    entry.add_to_hass(hass)
    settings = FadeSettings(hass, entry)
    notifications = []
    settings.add_listener(lambda: notifications.append(True))
    async with settings._lock:
        pending = asyncio.create_task(
            getattr(settings, method)(fixture.unique_id, value)
        )
        await asyncio.sleep(0)
        hass.config_entries.async_remove_subentry(entry, fixture.subentry_id)
        sibling_data = dict(entry.subentries[sibling.subentry_id].data)
    with pytest.raises(UnknownSubEntry):
        await pending
    assert set(entry.subentries) == {sibling.subentry_id}
    assert dict(entry.subentries[sibling.subentry_id].data) == sibling_data
    assert notifications == []
    with pytest.raises(UnknownSubEntry):
        settings.fixture_settings(fixture.unique_id)


@pytest.mark.parametrize(
    ("key", "value", "reason"),
    [
        ("global_fade_time", True, "invalid_fade_time"),
        ("fade_time", "bad", "invalid_fade_time"),
        ("fade_source", "bad", "invalid_fade_source"),
    ],
)
async def test_malformed_stored_settings_fail_controlled(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    *,
    key: str,
    value: object,
    reason: str,
) -> None:
    """Existing malformed data cannot silently select an arbitrary fade."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    if key == "global_fade_time":
        hass.config_entries.async_update_entry(
            entry, options=dict(entry.options) | {key: value}
        )
    else:
        current = entry.subentries[fixture.subentry_id]
        hass.config_entries.async_update_subentry(
            entry, current, data=dict(current.data) | {key: value}
        )
    settings = FadeSettings(hass, entry)
    if key == "global_fade_time":
        with pytest.raises(FixtureValidationError, match=rf"^{reason}$"):
            _ = settings.global_fade_time
    else:
        with pytest.raises(FixtureValidationError, match=rf"^{reason}$"):
            settings.fixture_settings(fixture.unique_id)


async def test_queued_setters_merge_data_changed_while_waiting(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Blocked setters read fresh options and fixture fields after admission."""
    fixture = make_fixture_subentry()
    entry = make_usb_dmx_entry(fixture)
    entry.add_to_hass(hass)
    settings = FadeSettings(hass, entry)
    async with settings._lock:
        global_write = asyncio.create_task(settings.async_set_global_fade_time(1))
        fixture_write = asyncio.create_task(
            settings.async_set_fixture_fade_time(fixture.unique_id, 0.5)
        )
        await asyncio.sleep(0)
        hass.config_entries.async_update_entry(
            entry,
            options=dict(entry.options)
            | {"startup_behavior": "zero", "future": "latest"},
        )
        current = entry.subentries[fixture.subentry_id]
        hass.config_entries.async_update_subentry(
            entry,
            current,
            data=dict(current.data)
            | {"address": 8, "fade_source": "individual", "future": "latest"},
        )
    await asyncio.gather(global_write, fixture_write)
    assert entry.options["startup_behavior"] == "zero"
    assert entry.options["future"] == "latest"
    assert entry.options["global_fade_time"] == 1.0
    assert current.data["address"] == 8
    assert current.data["future"] == "latest"
    assert settings.fixture_settings(fixture.unique_id) == (0.5, "individual")
