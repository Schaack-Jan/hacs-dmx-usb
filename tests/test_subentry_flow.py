"""Tests for USB DMX fixture config subentries."""

from __future__ import annotations

from types import MappingProxyType
from typing import TYPE_CHECKING, Any
from unittest.mock import patch
from uuid import UUID, uuid4

import pytest
from homeassistant import config_entries
from homeassistant.config_entries import ConfigSubentry
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.usb_dmx.config_flow import (
    FixtureSubentryFlow,
    UsbDmxConfigFlow,
)
from custom_components.usb_dmx.const import (
    BACKEND_SERIAL_PRO,
    CONF_BACKEND,
    CONF_DEVICE,
    CONF_INTERFACE_ID,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)

FIXTURE_TYPE = "type"
FIXTURE_NAME = "name"
FIXTURE_ADDRESS = "address"
FIXTURE_MINIMUM = "minimum"
FIXTURE_MAXIMUM = "maximum"


def _entry(*, subentries: tuple[ConfigSubentry, ...] = ()) -> MockConfigEntry:
    """Build a representative parent entry with optional fixture subentries."""
    return MockConfigEntry(
        domain=DOMAIN,
        data={
            CONF_BACKEND: BACKEND_SERIAL_PRO,
            CONF_DEVICE: "/dev/serial/by-id/dmx-test",
            CONF_INTERFACE_ID: "dmx-test",
        },
        unique_id="dmx-test",
        subentries_data=tuple(subentry.as_dict() for subentry in subentries),
    )


def _fixture_subentry(
    *,
    fixture_id: str | None = None,
    fixture_type: str = "dimmer",
    name: str = "Fixture",
    address: int = 1,
    minimum: int = 0,
    maximum: int = 255,
) -> ConfigSubentry:
    """Build one stored fixture subentry."""
    fixture_id = fixture_id or str(uuid4())
    return ConfigSubentry(
        data=MappingProxyType(
            {
                "id": fixture_id,
                FIXTURE_TYPE: fixture_type,
                FIXTURE_NAME: name,
                FIXTURE_ADDRESS: address,
                FIXTURE_MINIMUM: minimum,
                FIXTURE_MAXIMUM: maximum,
            }
        ),
        subentry_type="fixture",
        title=name,
        unique_id=fixture_id,
    )


async def _start_fixture_flow(
    hass: HomeAssistant, entry: MockConfigEntry
) -> config_entries.SubentryFlowResult:
    """Start a fixture creation flow for an added parent entry."""
    return await hass.config_entries.subentries.async_init(
        (entry.entry_id, "fixture"),
        context={"source": config_entries.SOURCE_USER},
    )


def _suggested_values(result: config_entries.SubentryFlowResult) -> dict[str, Any]:
    """Extract suggested form values without depending on selector internals."""
    return {
        marker.schema: marker.description["suggested_value"]
        for marker in result["data_schema"].schema
        if marker.description and "suggested_value" in marker.description
    }


def test_config_flow_advertises_only_fixture_subentries() -> None:
    """The parent flow exposes exactly the fixture subentry handler."""
    entry = _entry()

    assert UsbDmxConfigFlow.async_get_supported_subentry_types(entry) == {
        "fixture": FixtureSubentryFlow
    }


async def test_create_dimmer_and_raw_fixtures_with_uuid4_boundaries(
    hass: HomeAssistant,
) -> None:
    """Creation stores canonical UUID4 identities and preserves existing siblings."""
    entry = _entry()
    entry.add_to_hass(hass)

    fixtures = (
        {
            FIXTURE_TYPE: "dimmer",
            FIXTURE_NAME: "Front",
            FIXTURE_ADDRESS: 1,
            FIXTURE_MINIMUM: 0,
            FIXTURE_MAXIMUM: 255,
        },
        {
            FIXTURE_TYPE: "raw",
            FIXTURE_NAME: "Relay",
            FIXTURE_ADDRESS: 512,
            FIXTURE_MINIMUM: 0,
            FIXTURE_MAXIMUM: 255,
        },
    )

    first_subentry_id: str | None = None
    for expected_count, fixture in enumerate(fixtures, start=1):
        form = await _start_fixture_flow(hass, entry)
        with patch.object(hass.config_entries, "async_schedule_reload") as reload_mock:
            result = await hass.config_entries.subentries.async_configure(
                form["flow_id"], fixture
            )

        assert result["type"] is FlowResultType.CREATE_ENTRY
        reload_mock.assert_called_once_with(entry.entry_id)
        assert len(entry.subentries) == expected_count
        stored = next(
            subentry
            for subentry in entry.subentries.values()
            if subentry.unique_id == result["unique_id"]
        )
        identity = UUID(stored.unique_id)
        assert identity.version == 4
        assert str(identity) == stored.unique_id == stored.data["id"]
        assert stored.title == fixture[FIXTURE_NAME]
        assert dict(stored.data) == {"id": stored.unique_id, **fixture}
        if first_subentry_id is None:
            first_subentry_id = stored.subentry_id
        else:
            assert first_subentry_id in entry.subentries


@pytest.mark.parametrize(
    ("existing", "user_input", "error"),
    [
        (
            (),
            {
                FIXTURE_TYPE: "dimmer",
                FIXTURE_NAME: "Zero",
                FIXTURE_ADDRESS: 0,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 255,
            },
            "invalid_address",
        ),
        (
            (),
            {
                FIXTURE_TYPE: "dimmer",
                FIXTURE_NAME: "High",
                FIXTURE_ADDRESS: 513,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 255,
            },
            "invalid_address",
        ),
        (
            (_fixture_subentry(address=7),),
            {
                FIXTURE_TYPE: "raw",
                FIXTURE_NAME: "Other",
                FIXTURE_ADDRESS: 7,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 255,
            },
            "duplicate_address",
        ),
        (
            (_fixture_subentry(name="  Front Light "),),
            {
                FIXTURE_TYPE: "raw",
                FIXTURE_NAME: "front light",
                FIXTURE_ADDRESS: 8,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 255,
            },
            "duplicate_name",
        ),
        (
            (),
            {
                FIXTURE_TYPE: "dimmer",
                FIXTURE_NAME: "   ",
                FIXTURE_ADDRESS: 8,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 255,
            },
            "invalid_fixture",
        ),
        (
            (),
            {
                FIXTURE_TYPE: "rgb",
                FIXTURE_NAME: "Unknown",
                FIXTURE_ADDRESS: 8,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 255,
            },
            "invalid_fixture",
        ),
        (
            (),
            {
                FIXTURE_TYPE: "dimmer",
                FIXTURE_NAME: "Low",
                FIXTURE_ADDRESS: 8,
                FIXTURE_MINIMUM: -1,
                FIXTURE_MAXIMUM: 255,
            },
            "invalid_range",
        ),
        (
            (),
            {
                FIXTURE_TYPE: "dimmer",
                FIXTURE_NAME: "High",
                FIXTURE_ADDRESS: 8,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 256,
            },
            "invalid_range",
        ),
        (
            (),
            {
                FIXTURE_TYPE: "dimmer",
                FIXTURE_NAME: "Order",
                FIXTURE_ADDRESS: 8,
                FIXTURE_MINIMUM: 200,
                FIXTURE_MAXIMUM: 100,
            },
            "invalid_range",
        ),
    ],
)
async def test_create_rejects_invalid_fixture_and_preserves_form_values(
    hass: HomeAssistant,
    existing: tuple[ConfigSubentry, ...],
    user_input: dict[str, Any],
    error: str,
) -> None:
    """Invalid fixtures return stable errors without discarding entered values."""
    entry = _entry(subentries=existing)
    entry.add_to_hass(hass)
    form = await _start_fixture_flow(hass, entry)

    result = await hass.config_entries.subentries.async_configure(
        form["flow_id"], user_input
    )

    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "user"
    assert result["errors"] == {"base": error}
    assert _suggested_values(result) == user_input
    assert len(entry.subentries) == len(existing)


async def test_create_rejects_malformed_stored_sibling(
    hass: HomeAssistant,
) -> None:
    """Every stored sibling must pass the model storage boundary."""
    malformed = _fixture_subentry(fixture_id="not-a-uuid")
    entry = _entry(subentries=(malformed,))
    entry.add_to_hass(hass)
    form = await _start_fixture_flow(hass, entry)

    result = await hass.config_entries.subentries.async_configure(
        form["flow_id"],
        {
            FIXTURE_TYPE: "raw",
            FIXTURE_NAME: "Valid proposal",
            FIXTURE_ADDRESS: 2,
            FIXTURE_MINIMUM: 0,
            FIXTURE_MAXIMUM: 255,
        },
    )

    assert result["type"] is FlowResultType.FORM
    assert result["errors"] == {"base": "invalid_fixture"}
    assert set(entry.subentries) == {malformed.subentry_id}


async def test_reconfigure_updates_fixture_without_changing_identity_or_siblings(
    hass: HomeAssistant,
) -> None:
    """Reconfigure replaces fixture data while preserving both identities."""
    fixture = _fixture_subentry(name="Front", address=1)
    sibling = _fixture_subentry(
        fixture_type="raw", name="Relay", address=512, minimum=10, maximum=200
    )
    entry = _entry(subentries=(fixture, sibling))
    entry.add_to_hass(hass)
    stored_fixture = entry.subentries[fixture.subentry_id]
    stored_sibling = entry.subentries[sibling.subentry_id]
    old_data = stored_fixture.data
    sibling_data = stored_sibling.data
    form = await entry.start_subentry_reconfigure_flow(hass, fixture.subentry_id)
    assert _suggested_values(form) == {
        FIXTURE_TYPE: "dimmer",
        FIXTURE_NAME: "Front",
        FIXTURE_ADDRESS: 1,
        FIXTURE_MINIMUM: 0,
        FIXTURE_MAXIMUM: 255,
    }

    with patch.object(hass.config_entries, "async_schedule_reload") as reload_mock:
        result = await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            {
                FIXTURE_TYPE: "raw",
                FIXTURE_NAME: "Front updated",
                FIXTURE_ADDRESS: 2,
                FIXTURE_MINIMUM: 5,
                FIXTURE_MAXIMUM: 240,
            },
        )

    assert result["type"] is FlowResultType.ABORT
    assert result["reason"] == "reconfigure_successful"
    updated = entry.subentries[fixture.subentry_id]
    assert updated is stored_fixture
    assert updated.subentry_type == "fixture"
    assert updated.unique_id == fixture.unique_id == updated.data["id"]
    assert updated.title == "Front updated"
    assert updated.data is not old_data
    assert dict(updated.data) == {
        "id": fixture.unique_id,
        FIXTURE_TYPE: "raw",
        FIXTURE_NAME: "Front updated",
        FIXTURE_ADDRESS: 2,
        FIXTURE_MINIMUM: 5,
        FIXTURE_MAXIMUM: 240,
    }
    assert entry.subentries[sibling.subentry_id] is stored_sibling
    assert stored_sibling.data is sibling_data
    reload_mock.assert_called_once_with(entry.entry_id)


async def test_reconfigure_excludes_current_fixture_but_checks_siblings(
    hass: HomeAssistant,
) -> None:
    """A fixture may retain its values but may not collide with a sibling."""
    fixture = _fixture_subentry(name="Front", address=1)
    sibling = _fixture_subentry(name="Back", address=2)
    entry = _entry(subentries=(fixture, sibling))
    entry.add_to_hass(hass)

    form = await entry.start_subentry_reconfigure_flow(hass, fixture.subentry_id)
    with patch.object(hass.config_entries, "async_schedule_reload"):
        unchanged = await hass.config_entries.subentries.async_configure(
            form["flow_id"],
            {
                FIXTURE_TYPE: "dimmer",
                FIXTURE_NAME: "Front",
                FIXTURE_ADDRESS: 1,
                FIXTURE_MINIMUM: 0,
                FIXTURE_MAXIMUM: 255,
            },
        )
    assert unchanged["type"] is FlowResultType.ABORT

    form = await entry.start_subentry_reconfigure_flow(hass, fixture.subentry_id)
    collision = await hass.config_entries.subentries.async_configure(
        form["flow_id"],
        {
            FIXTURE_TYPE: "dimmer",
            FIXTURE_NAME: " back ",
            FIXTURE_ADDRESS: 1,
            FIXTURE_MINIMUM: 0,
            FIXTURE_MAXIMUM: 255,
        },
    )
    assert collision["type"] is FlowResultType.FORM
    assert collision["errors"] == {"base": "duplicate_name"}

    retry = await hass.config_entries.subentries.async_configure(
        form["flow_id"],
        {
            FIXTURE_TYPE: "dimmer",
            FIXTURE_NAME: "Front",
            FIXTURE_ADDRESS: 2,
            FIXTURE_MINIMUM: 0,
            FIXTURE_MAXIMUM: 255,
        },
    )
    assert retry["type"] is FlowResultType.FORM
    assert retry["errors"] == {"base": "duplicate_address"}


@pytest.mark.usefixtures("_mock_entry_setup")
async def test_remove_subentry_clears_only_owned_registry_entity(
    hass: HomeAssistant,
) -> None:
    """HA 2026.9 removes registry records owned by the deleted subentry."""
    fixture = _fixture_subentry(name="Front", address=1)
    sibling = _fixture_subentry(name="Back", address=2)
    entry = _entry(subentries=(fixture, sibling))
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    registry = er.async_get(hass)
    removed = registry.async_get_or_create(
        "light",
        DOMAIN,
        "removed-fixture",
        config_entry=entry,
        config_subentry_id=fixture.subentry_id,
        suggested_object_id="removed_fixture",
    )
    kept = registry.async_get_or_create(
        "light",
        DOMAIN,
        "kept-fixture",
        config_entry=entry,
        config_subentry_id=sibling.subentry_id,
        suggested_object_id="kept_fixture",
    )

    assert hass.config_entries.async_remove_subentry(entry, fixture.subentry_id)
    await hass.async_block_till_done()

    assert fixture.subentry_id not in entry.subentries
    assert sibling.subentry_id in entry.subentries
    assert registry.async_get(removed.entity_id) is None
    assert registry.async_get(kept.entity_id) is kept
