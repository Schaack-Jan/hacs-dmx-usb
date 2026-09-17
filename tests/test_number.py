"""Tests for USB DMX raw-channel number entities."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from homeassistant.components.number import NumberMode
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_platform as ep
from homeassistant.helpers import entity_registry as er

from custom_components.usb_dmx import UsbDmxRuntime
from custom_components.usb_dmx.const import DOMAIN
from custom_components.usb_dmx.controller import DmxController
from custom_components.usb_dmx.light import (
    UsbDmxDimmer,
)
from custom_components.usb_dmx.light import (
    async_setup_entry as async_setup_light_entry,
)
from custom_components.usb_dmx.models import (
    FixtureConfig,
    FixtureType,
    FixtureValidationError,
    StartupBehavior,
)
from custom_components.usb_dmx.number import UsbDmxRawChannel, async_setup_entry

from .fakes import FakeBackend, ObservedSemaphore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)

DIMMER_ID = "11111111-1111-4111-8111-111111111111"
RAW_ID = "33333333-3333-4333-8333-333333333333"
SECOND_RAW_ID = "44444444-4444-4444-8444-444444444444"


async def _runtime_entry(
    entry: MockConfigEntry,
) -> tuple[FakeBackend, DmxController]:
    """Attach and start a shared runtime on an entry."""
    backend = FakeBackend()
    controller = DmxController(backend)
    await controller.async_start()
    await controller.async_send_current_frame()
    entry.runtime_data = UsbDmxRuntime(controller, backend, StartupBehavior.ZERO)
    return backend, controller


def _raw_fixture(
    *,
    fixture_id: str = RAW_ID,
    address: int = 20,
    minimum: int = 0,
    maximum: int = 255,
) -> FixtureConfig:
    """Build a validated raw fixture."""
    return FixtureConfig(
        fixture_id=fixture_id,
        fixture_type=FixtureType.RAW,
        name="Relay",
        address=address,
        minimum=minimum,
        maximum=maximum,
    )


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Yield until a synchronous condition becomes true."""
    async with asyncio.timeout(1):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0)


async def test_number_platform_filters_fixtures_and_preserves_subentry_ownership(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Only raw fixtures are added and every callback carries its exact owner."""
    dimmer = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    first = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=2
    )
    second = make_fixture_subentry(
        fixture_id=SECOND_RAW_ID,
        fixture_type="raw",
        name="Shutter",
        address=3,
    )
    entry = make_usb_dmx_entry(dimmer, first, second)
    entry.add_to_hass(hass)
    await _runtime_entry(entry)
    added: list[tuple[UsbDmxRawChannel, str | None]] = []

    def add_entities(
        entities: list[UsbDmxRawChannel],
        update_before_add: bool = False,  # noqa: FBT001, FBT002
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        assert not update_before_add
        assert len(entities) == 1
        added.append((entities[0], config_subentry_id))

    await async_setup_entry(hass, entry, add_entities)

    assert [(entity.unique_id, owner) for entity, owner in added] == [
        (f"{entry.entry_id}_{RAW_ID}", first.subentry_id),
        (f"{entry.entry_id}_{SECOND_RAW_ID}", second.subentry_id),
    ]
    await entry.runtime_data.controller.async_stop()


async def test_number_and_light_share_one_physical_device(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Both platforms attach their stable entities to one interface device."""
    dimmer = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front")
    raw = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=20
    )
    entry = make_usb_dmx_entry(dimmer, raw)
    entry.add_to_hass(hass)
    _, controller = await _runtime_entry(entry)
    lights: list[UsbDmxDimmer] = []
    numbers: list[UsbDmxRawChannel] = []

    def add_lights(
        entities: list[UsbDmxDimmer],
        _update_before_add: bool = False,  # noqa: FBT001, FBT002
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        assert config_subentry_id == dimmer.subentry_id
        lights.extend(entities)

    def add_numbers(
        entities: list[UsbDmxRawChannel],
        _update_before_add: bool = False,  # noqa: FBT001, FBT002
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        assert config_subentry_id == raw.subentry_id
        numbers.extend(entities)

    await async_setup_light_entry(hass, entry, add_lights)
    await async_setup_entry(hass, entry, add_numbers)
    light = lights[0]
    number = numbers[0]

    assert light.device_entry is number.device_entry
    assert light.device_entry.config_subentry_id is None
    assert light.device_entry.identifiers == {(DOMAIN, entry.entry_id)}
    assert light.device_entry.name == "USB DMX interface"
    assert number.unique_id == f"{entry.entry_id}_{RAW_ID}"
    assert number.name == "Relay"
    assert number.translation_key is None
    assert number.native_min_value == 0
    assert number.native_max_value == 255
    assert number.native_step == 1
    assert number.mode is NumberMode.BOX
    await controller.async_stop()


@pytest.mark.parametrize("value", [0, 12, 12.0, 255])
async def test_raw_channel_accepts_integral_values_and_reads_controller_state(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    value: float,
) -> None:
    """Integral raw values write unchanged without dimmer range scaling."""
    entry = make_usb_dmx_entry(
        make_fixture_subentry(fixture_id=RAW_ID, fixture_type="raw", address=20)
    )
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxRawChannel(_raw_fixture(minimum=100, maximum=150), entry)

    await entity.async_set_native_value(value)

    expected = int(value)
    assert controller.current_frame[19] == expected
    assert backend.frames[-1][19] == expected
    assert entity.native_value == expected
    assert type(entity.native_value) is int
    await controller.async_stop()


@pytest.mark.parametrize(
    "value", [True, False, 1.5, -1, 256, math.inf, math.nan, "12", None]
)
async def test_raw_channel_rejects_nonintegral_or_out_of_range_values(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    value: Any,
) -> None:
    """Invalid direct values raise without mutating or writing the universe."""
    entry = make_usb_dmx_entry(
        make_fixture_subentry(fixture_id=RAW_ID, fixture_type="raw", address=20)
    )
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxRawChannel(_raw_fixture(), entry)

    with pytest.raises((TypeError, ValueError), match="value"):
        await entity.async_set_native_value(value)

    assert entity.native_value == 0
    assert controller.current_frame == bytes(512)
    assert backend.frames == [bytes(512)]
    await controller.async_stop()


async def test_raw_availability_mirrors_shared_controller(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Raw entity availability follows the same physical transport."""
    entry = make_usb_dmx_entry(
        make_fixture_subentry(fixture_id=RAW_ID, fixture_type="raw", address=20)
    )
    _, controller = await _runtime_entry(entry)
    entity = UsbDmxRawChannel(_raw_fixture(), entry)

    assert entity.available
    await controller.async_stop()
    assert not entity.available


async def test_number_platform_rejects_mismatched_fixture_identity(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A raw subentry with split identities cannot create an entity."""
    stored = make_fixture_subentry(
        fixture_id=RAW_ID,
        unique_id=SECOND_RAW_ID,
        fixture_type="raw",
    )
    entry = make_usb_dmx_entry(stored)
    await _runtime_entry(entry)
    add_entities = MagicMock()

    with pytest.raises(FixtureValidationError, match="invalid_fixture"):
        await async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()
    await entry.runtime_data.controller.async_stop()


async def test_number_platform_validates_dimmer_collisions_before_filter(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A collision among filtered-out dimmers still aborts number setup."""
    first = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    second = make_fixture_subentry(fixture_id=SECOND_RAW_ID, name="Back", address=1)
    raw = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=20
    )
    entry = make_usb_dmx_entry(first, second, raw)
    entry.add_to_hass(hass)
    await _runtime_entry(entry)
    add_entities = MagicMock()

    with pytest.raises(FixtureValidationError, match="duplicate_address"):
        await async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()
    await entry.runtime_data.controller.async_stop()


async def test_loaded_subentry_deletion_removes_owned_entity_only(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """HA removes a deleted fixture's live state and registry ownership only."""
    dimmer = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    raw = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=20
    )
    entry = make_usb_dmx_entry(dimmer, raw, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        controller = entry.runtime_data.controller
        devices = dr.async_entries_for_config_entry(dr.async_get(hass), entry.entry_id)
        assert len(devices) == 1
        assert devices[0].config_subentry_id is None
        registry = er.async_get(hass)
        light_id = registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.entry_id}_{DIMMER_ID}"
        )
        number_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_{RAW_ID}"
        )
        identities = [
            (item.domain, item.platform, item.unique_id, item.entity_id)
            for item in registry.entities.values()
        ]
        assert light_id is not None, identities
        assert number_id is not None, identities
        assert hass.states.get(light_id) is not None
        assert hass.states.get(number_id) is not None

        await hass.services.async_call(
            "number",
            "set_value",
            {"value": 255},
            target={"entity_id": number_id},
            blocking=True,
        )
        assert controller.current_frame[19] == 255
        assert len(controller._availability_listeners) == 2

        assert hass.config_entries.async_remove_subentry(entry, raw.subentry_id)
        await hass.async_block_till_done()

        assert raw.subentry_id not in entry.subentries
        assert dimmer.subentry_id in entry.subentries
        assert hass.states.get(number_id) is None
        assert registry.async_get(number_id) is None
        assert hass.states.get(light_id) is not None
        assert registry.async_get(light_id) is not None
        assert controller.current_frame[19] == 0
        assert backend.frames[-1][19] == 0
        assert len(controller._availability_listeners) == 1

        await hass.services.async_call(
            "light",
            "turn_on",
            {"brightness": 128},
            target={"entity_id": light_id},
            blocking=True,
        )
        assert backend.frames[-1][0] == 128
        assert backend.frames[-1][19] == 0

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_running_and_queued_number_commands_cannot_outlive_fixture_removal(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Removal wins after an active write and rejects its queued successor."""
    dimmer = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    raw = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=20
    )
    entry = make_usb_dmx_entry(dimmer, raw, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        controller = entry.runtime_data.controller
        registry = er.async_get(hass)
        number_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_{RAW_ID}"
        )
        light_id = registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.entry_id}_{DIMMER_ID}"
        )
        assert number_id is not None
        assert light_id is not None
        entity = next(
            platform.entities[number_id]
            for platform in ep.async_get_platforms(hass, DOMAIN)
            if number_id in platform.entities
        )
        assert isinstance(entity, UsbDmxRawChannel)
        service_gate = ObservedSemaphore(1, observe_acquire=2)
        entity.parallel_updates = service_gate
        backend.send_gate = asyncio.Event()
        backend.send_started.clear()

        active = asyncio.create_task(
            hass.services.async_call(
                "number",
                "set_value",
                {"value": 64},
                target={"entity_id": number_id},
                blocking=True,
            )
        )
        await backend.send_started.wait()
        assert controller.current_frame[19] == 64

        queued = asyncio.create_task(
            hass.services.async_call(
                "number",
                "set_value",
                {"value": 255},
                target={"entity_id": number_id},
                blocking=True,
            )
        )
        await service_gate.acquire_started.wait()

        assert hass.config_entries.async_remove_subentry(entry, raw.subentry_id)
        await _wait_until(lambda: len(controller._availability_listeners) == 1)
        backend.send_gate.set()

        await asyncio.gather(active, queued)
        await hass.async_block_till_done()

        assert controller.current_frame[19] == 0
        assert backend.frames[-1][19] == 0
        assert hass.states.get(number_id) is None
        assert registry.async_get(number_id) is None
        assert hass.states.get(light_id) is not None
        assert registry.async_get(light_id) is not None
        assert len(controller._availability_listeners) == 1

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_normal_unload_preserves_fixture_slot_without_blackout(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Platform unload does not clear a fixture whose subentry still exists."""
    raw = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=20
    )
    entry = make_usb_dmx_entry(raw, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_{RAW_ID}"
        )
        assert entity_id is not None
        controller = entry.runtime_data.controller
        entity = next(
            platform.entities[entity_id]
            for platform in ep.async_get_platforms(hass, DOMAIN)
            if entity_id in platform.entities
        )
        assert isinstance(entity, UsbDmxRawChannel)

        await hass.services.async_call(
            "number",
            "set_value",
            {"value": 255},
            target={"entity_id": entity_id},
            blocking=True,
        )
        frames_before = list(backend.frames)

        assert await hass.config_entries.async_unload(entry.entry_id)

        assert raw.subentry_id in entry.subentries
        assert controller.current_frame[19] == 255
        assert backend.frames == frames_before

        await entity.async_set_native_value(1)
        assert controller.current_frame[19] == 255
        assert backend.frames == frames_before


async def test_number_service_publishes_value_and_rejection_writes_nothing(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Accepted values update HA immediately while rejected values do not write."""
    raw = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=20
    )
    entry = make_usb_dmx_entry(raw, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with patch(
        "custom_components.usb_dmx.async_create_backend",
        AsyncMock(return_value=backend),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_{RAW_ID}"
        )
        assert entity_id is not None

        await hass.services.async_call(
            "number",
            "set_value",
            {"value": 42},
            target={"entity_id": entity_id},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == "42"

        frames_before = list(backend.frames)
        state_before = state
        with pytest.raises(ValueError, match="value"):
            await hass.services.async_call(
                "number",
                "set_value",
                {"value": 1.5},
                target={"entity_id": entity_id},
                blocking=True,
            )
        assert backend.frames == frames_before
        assert hass.states.get(entity_id) is state_before

        assert await hass.config_entries.async_unload(entry.entry_id)
