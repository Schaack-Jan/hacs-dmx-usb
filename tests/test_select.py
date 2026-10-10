"""Strict native source selector calls and setting retention."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

import pytest

from custom_components.usb_dmx.models import StartupBehavior

from .fakes import FakeBackend
from .test_dimmer_entities import _entity, _ids

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)


@pytest.mark.parametrize("option", [True, None, 0, "Global", " individual", "other"])
async def test_source_rejects_invalid_direct_options(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    option: object,
) -> None:
    """A source is an exact stable string, never coerced or normalized."""
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
        entity = _entity(hass, ids["fade_source"])
        data = dict(entry.subentries[fixture.subentry_id].data)
        with pytest.raises(ValueError, match="invalid_fade_source"):
            await entity.async_select_option(option)
        assert dict(entry.subentries[fixture.subentry_id].data) == data
        assert backend.frames == [bytes(512)]
        assert hass.states.get(ids["fade_source"]).state == "global"
        assert await hass.config_entries.async_unload(entry.entry_id)
