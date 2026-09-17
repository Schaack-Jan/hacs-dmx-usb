"""Tests for USB DMX dimmer light entities."""

from __future__ import annotations

import asyncio
import math
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

import pytest
from homeassistant.components.light import (
    ATTR_BRIGHTNESS,
    ATTR_TRANSITION,
    ColorMode,
    LightEntityFeature,
)
from homeassistant.const import (
    STATE_OFF,
    STATE_ON,
    STATE_UNAVAILABLE,
    STATE_UNKNOWN,
    Platform,
)
from homeassistant.core import State
from homeassistant.helpers import entity_platform as ep
from homeassistant.helpers import entity_registry as er
from pytest_homeassistant_custom_component.common import mock_restore_cache

from custom_components.usb_dmx import UsbDmxRuntime
from custom_components.usb_dmx.const import DOMAIN
from custom_components.usb_dmx.controller import DmxController
from custom_components.usb_dmx.entity import interface_device_info
from custom_components.usb_dmx.light import UsbDmxDimmer, async_setup_entry
from custom_components.usb_dmx.models import (
    FixtureConfig,
    FixtureType,
    FixtureValidationError,
    StartupBehavior,
)

from .fakes import FakeBackend, ObservedSemaphore

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant
    from pytest_homeassistant_custom_component.common import MockConfigEntry

pytestmark = pytest.mark.usefixtures(
    "enable_custom_integrations", "_mock_usb_dependency"
)

DIMMER_ID = "11111111-1111-4111-8111-111111111111"
SECOND_DIMMER_ID = "22222222-2222-4222-8222-222222222222"
RAW_ID = "33333333-3333-4333-8333-333333333333"


async def _runtime_entry(
    entry: MockConfigEntry,
    *,
    backend: FakeBackend | None = None,
    startup_behavior: StartupBehavior = StartupBehavior.RESTORE,
) -> tuple[FakeBackend, DmxController]:
    """Attach and start a representative runtime on an entry."""
    backend = backend or FakeBackend()
    controller = DmxController(backend, reconnect_delays=(0,))
    await controller.async_start()
    await controller.async_send_current_frame()
    entry.runtime_data = UsbDmxRuntime(controller, backend, startup_behavior)
    return backend, controller


def _fixture(
    *,
    fixture_id: str = DIMMER_ID,
    address: int = 1,
    minimum: int = 0,
    maximum: int = 255,
) -> FixtureConfig:
    """Build a validated dimmer fixture."""
    return FixtureConfig(
        fixture_id=fixture_id,
        fixture_type=FixtureType.DIMMER,
        name="Front",
        address=address,
        minimum=minimum,
        maximum=maximum,
    )


async def _wait_until(predicate: Callable[[], bool]) -> None:
    """Yield until a synchronous condition becomes true."""
    async with asyncio.timeout(1):
        while not predicate():  # noqa: ASYNC110
            await asyncio.sleep(0)


async def test_light_platform_filters_fixtures_and_preserves_subentry_ownership(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Only dimmers are added and every callback carries its exact owner."""
    first = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    raw = make_fixture_subentry(
        fixture_id=RAW_ID, fixture_type="raw", name="Relay", address=2
    )
    second = make_fixture_subentry(fixture_id=SECOND_DIMMER_ID, name="Back", address=3)
    entry = make_usb_dmx_entry(first, raw, second)
    entry.add_to_hass(hass)
    await _runtime_entry(entry)
    added: list[tuple[UsbDmxDimmer, str | None]] = []

    def add_entities(
        entities: list[UsbDmxDimmer],
        update_before_add: bool = False,  # noqa: FBT001, FBT002
        *,
        config_subentry_id: str | None = None,
    ) -> None:
        assert not update_before_add
        assert len(entities) == 1
        added.append((entities[0], config_subentry_id))

    await async_setup_entry(hass, entry, add_entities)

    assert [(entity.unique_id, owner) for entity, owner in added] == [
        (f"{entry.entry_id}_{DIMMER_ID}", first.subentry_id),
        (f"{entry.entry_id}_{SECOND_DIMMER_ID}", second.subentry_id),
    ]
    await entry.runtime_data.controller.async_stop()


def test_light_identity_and_device_are_stable_and_interface_scoped(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Mutable fixture data cannot alter entity or physical-device identity."""
    stored = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    entry = make_usb_dmx_entry(stored)
    entry.runtime_data = UsbDmxRuntime(
        DmxController(FakeBackend()), FakeBackend(), StartupBehavior.ZERO
    )
    entity = UsbDmxDimmer(_fixture(), entry)
    renamed = UsbDmxDimmer(
        FixtureConfig(
            fixture_id=DIMMER_ID,
            fixture_type=FixtureType.DIMMER,
            name="Renamed",
            address=512,
            minimum=10,
            maximum=200,
        ),
        entry,
    )

    assert entity.unique_id == renamed.unique_id == f"{entry.entry_id}_{DIMMER_ID}"
    device_info = interface_device_info(entry)
    assert device_info["identifiers"] == {(DOMAIN, entry.entry_id)}
    assert device_info["name"] == "Studio Interface"
    assert device_info["translation_key"] == "interface"
    assert "/dev/" not in repr(device_info)
    assert entity.name == "Front"
    assert entity.translation_key is None
    assert entity.color_mode is ColorMode.BRIGHTNESS
    assert entity.supported_color_modes == {ColorMode.BRIGHTNESS}
    assert entity.supported_features == LightEntityFeature.TRANSITION


@pytest.mark.parametrize(
    ("brightness", "expected_dmx", "expected_on"),
    [(0, 0, False), (1, 10, True), (128, 105, True), (255, 200, True)],
)
async def test_brightness_maps_to_configured_range_and_updates_current_state(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    brightness: int,
    expected_dmx: int,
    *,
    expected_on: bool,
) -> None:
    """HA brightness maps deterministically while zero has off semantics."""
    entry = make_usb_dmx_entry(make_fixture_subentry(fixture_id=DIMMER_ID))
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxDimmer(_fixture(minimum=10, maximum=200), entry)

    await entity.async_turn_on(**{ATTR_BRIGHTNESS: brightness})

    assert controller.current_frame[0] == expected_dmx
    assert backend.frames[-1][0] == expected_dmx
    assert entity.is_on is expected_on
    assert entity.brightness == (brightness or None)
    await controller.async_stop()


async def test_default_mapping_last_nonzero_and_off_semantics(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Off preserves the last level and an unspecified on reuses it."""
    entry = make_usb_dmx_entry(make_fixture_subentry(fixture_id=DIMMER_ID))
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxDimmer(_fixture(), entry)

    await entity.async_turn_on(**{ATTR_BRIGHTNESS: 1})
    assert backend.frames[-1][0] == 0
    assert entity.is_on
    assert entity.brightness == 1

    await entity.async_turn_off()
    assert backend.frames[-1][0] == 0
    assert not entity.is_on
    assert entity.brightness == 1

    await entity.async_turn_on(**{ATTR_BRIGHTNESS: None})
    assert entity.is_on
    assert entity.brightness == 1
    assert backend.frames[-1][0] == 0

    fresh = UsbDmxDimmer(_fixture(fixture_id=SECOND_DIMMER_ID, address=2), entry)
    await fresh.async_turn_on(**{ATTR_BRIGHTNESS: None})
    assert fresh.brightness == 255
    assert backend.frames[-1][1] == 255
    await controller.async_stop()


@pytest.mark.parametrize("brightness", [True, 1.0, -1, 256, "1"])
async def test_direct_light_methods_reject_malformed_brightness(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    brightness: Any,
) -> None:
    """Direct calls cannot clamp or coerce invalid brightness values."""
    entry = make_usb_dmx_entry(make_fixture_subentry(fixture_id=DIMMER_ID))
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxDimmer(_fixture(), entry)

    with pytest.raises((TypeError, ValueError), match="brightness"):
        await entity.async_turn_on(**{ATTR_BRIGHTNESS: brightness})

    assert controller.current_frame == bytes(512)
    assert backend.frames == [bytes(512)]
    await controller.async_stop()


async def test_on_and_off_transitions_forward_and_replace_through_controller(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """An off transition replaces the active on transition at its current value."""
    entry = make_usb_dmx_entry(make_fixture_subentry(fixture_id=DIMMER_ID))
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxDimmer(_fixture(), entry)

    await entity.async_turn_on(**{ATTR_BRIGHTNESS: 255, ATTR_TRANSITION: 0.2})
    await asyncio.sleep(0.08)
    replacement_start = controller.current_frame[0]
    assert 0 < replacement_start < 255

    await entity.async_turn_off(**{ATTR_TRANSITION: 0.05})
    await asyncio.sleep(0.15)

    assert controller.current_frame[0] == 0
    assert backend.frames[-1][0] == 0
    assert all(frame[0] != 255 for frame in backend.frames)
    assert not entity.is_on
    assert entity.brightness == 255
    await controller.async_stop()


@pytest.mark.parametrize("transition", [True, -1, math.inf, math.nan, 6554, "1"])
async def test_direct_light_methods_reject_invalid_transitions(
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    transition: Any,
) -> None:
    """Direct calls enforce Home Assistant's finite transition range."""
    entry = make_usb_dmx_entry(make_fixture_subentry(fixture_id=DIMMER_ID))
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxDimmer(_fixture(), entry)

    with pytest.raises((TypeError, ValueError), match="transition"):
        await entity.async_turn_on(**{ATTR_TRANSITION: transition})
    with pytest.raises((TypeError, ValueError), match="transition"):
        await entity.async_turn_off(**{ATTR_TRANSITION: transition})

    assert backend.frames == [bytes(512)]
    await controller.async_stop()


@pytest.mark.parametrize(
    ("state", "attributes"),
    [
        (STATE_UNKNOWN, {ATTR_BRIGHTNESS: 50}),
        (STATE_UNAVAILABLE, {ATTR_BRIGHTNESS: 50}),
        ("broken", {ATTR_BRIGHTNESS: 50}),
        (STATE_ON, {}),
        (STATE_ON, {ATTR_BRIGHTNESS: True}),
        (STATE_ON, {ATTR_BRIGHTNESS: 0}),
        (STATE_ON, {ATTR_BRIGHTNESS: 256}),
        (STATE_ON, {ATTR_BRIGHTNESS: "50"}),
    ],
)
async def test_restore_ignores_unknown_or_malformed_state_without_sending(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    state: str,
    attributes: dict[str, Any],
) -> None:
    """Only a valid saved HA light state can mutate the zeroed controller."""
    entry = make_usb_dmx_entry(make_fixture_subentry(fixture_id=DIMMER_ID))
    backend, controller = await _runtime_entry(entry)
    entity = UsbDmxDimmer(_fixture(), entry)
    entity.hass = hass
    entity.entity_id = "light.front"

    with patch.object(
        entity,
        "async_get_last_state",
        AsyncMock(return_value=State(entity.entity_id, state, attributes)),
    ):
        await entity.async_added_to_hass()

    assert backend.frames == [bytes(512)]
    assert controller.current_frame == bytes(512)
    assert not entity.is_on
    assert entity.brightness is None
    await controller.async_stop()


async def test_restore_valid_on_and_off_states(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Valid on restores through the controller while off causes no send."""
    entry = make_usb_dmx_entry(make_fixture_subentry(fixture_id=DIMMER_ID))
    backend, controller = await _runtime_entry(entry)
    on_entity = UsbDmxDimmer(_fixture(minimum=10, maximum=200), entry)
    on_entity.hass = hass
    on_entity.entity_id = "light.front"
    with patch.object(
        on_entity,
        "async_get_last_state",
        AsyncMock(
            return_value=State(on_entity.entity_id, STATE_ON, {ATTR_BRIGHTNESS: 128})
        ),
    ):
        await on_entity.async_added_to_hass()

    assert backend.frames[-1][0] == 105
    assert on_entity.is_on
    assert on_entity.brightness == 128

    off_fixture = _fixture(fixture_id=SECOND_DIMMER_ID, address=2)
    off_entity = UsbDmxDimmer(off_fixture, entry)
    off_entity.hass = hass
    off_entity.entity_id = "light.back"
    frames_before = list(backend.frames)
    with patch.object(
        off_entity,
        "async_get_last_state",
        AsyncMock(
            return_value=State(off_entity.entity_id, STATE_OFF, {ATTR_BRIGHTNESS: 77})
        ),
    ):
        await off_entity.async_added_to_hass()

    assert backend.frames == frames_before
    assert not off_entity.is_on
    assert off_entity.brightness == 77
    assert controller.current_frame[1] == 0
    await controller.async_stop()


async def test_zero_startup_does_not_read_or_send_restore_state(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """ZERO startup leaves a deterministic frame without restore lookup."""
    entry = make_usb_dmx_entry(
        make_fixture_subentry(fixture_id=DIMMER_ID),
        startup_behavior=StartupBehavior.ZERO,
    )
    backend, controller = await _runtime_entry(
        entry, startup_behavior=StartupBehavior.ZERO
    )
    entity = UsbDmxDimmer(_fixture(), entry)
    entity.hass = hass
    entity.entity_id = "light.front"

    with patch.object(
        entity,
        "async_get_last_state",
        AsyncMock(side_effect=AssertionError("restore lookup was not expected")),
    ):
        await entity.async_added_to_hass()

    assert backend.frames == [bytes(512)]
    assert controller.current_frame == bytes(512)
    assert not entity.is_on
    await controller.async_stop()


async def test_transport_availability_updates_ha_and_listener_cleanup_on_reload(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Failure/reconnect writes HA state and unload removes the sole listener."""
    fixture = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    first_backend = FakeBackend()
    first_backend.reconnect_gate = asyncio.Event()
    second_backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(side_effect=[first_backend, second_backend]),
        ),
        patch("custom_components.usb_dmx.PLATFORMS", (Platform.LIGHT,)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        first_controller = entry.runtime_data.controller
        first_controller._reconnect_delays = (0,)
        registry_id = f"{entry.entry_id}_{DIMMER_ID}"
        registry = er.async_get(hass)
        registry_entry = registry.async_get_entity_id("light", DOMAIN, registry_id)
        assert registry_entry is not None
        state = hass.states.get(registry_entry)
        assert state is not None
        assert state.state == STATE_OFF
        assert len(first_controller._availability_listeners) == 1

        first_backend.send_failures = 1
        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_BRIGHTNESS: 100},
            target={"entity_id": state.entity_id},
            blocking=True,
        )
        await _wait_until(
            lambda: hass.states.get(state.entity_id).state == STATE_UNAVAILABLE
        )

        first_backend.reconnect_gate.set()
        await _wait_until(
            lambda: (
                first_controller.available
                and hass.states.get(state.entity_id).state == STATE_ON
            )
        )
        assert first_controller.current_frame[0] == 99

        assert await hass.config_entries.async_reload(entry.entry_id)
        assert len(first_controller._availability_listeners) == 0
        second_controller = entry.runtime_data.controller
        assert len(second_controller._availability_listeners) == 1
        assert second_controller is not first_controller
        assert registry.async_get_entity_id("light", DOMAIN, registry_id) is not None

        assert await hass.config_entries.async_unload(entry.entry_id)
        assert len(second_controller._availability_listeners) == 0


async def test_light_services_publish_direct_state_immediately(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """Successful direct commands immediately update Home Assistant state."""
    fixture = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch("custom_components.usb_dmx.PLATFORMS", (Platform.LIGHT,)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.entry_id}_{DIMMER_ID}"
        )
        assert entity_id is not None

        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_BRIGHTNESS: 91},
            target={"entity_id": entity_id},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_ON
        assert state.attributes[ATTR_BRIGHTNESS] == 91

        await hass.services.async_call(
            "light",
            "turn_off",
            target={"entity_id": entity_id},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_OFF

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_light_transition_service_publishes_target_state_immediately(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """An admitted transition publishes its requested HA state immediately."""
    fixture = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    entry = make_usb_dmx_entry(fixture, startup_behavior=StartupBehavior.ZERO)
    entry.add_to_hass(hass)
    backend = FakeBackend()

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch("custom_components.usb_dmx.PLATFORMS", (Platform.LIGHT,)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.entry_id}_{DIMMER_ID}"
        )
        assert entity_id is not None

        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_BRIGHTNESS: 200, ATTR_TRANSITION: 0.2},
            target={"entity_id": entity_id},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_ON
        assert state.attributes[ATTR_BRIGHTNESS] == 200
        assert entry.runtime_data.controller.current_frame[0] < 200

        await hass.services.async_call(
            "light",
            "turn_off",
            {ATTR_TRANSITION: 0.2},
            target={"entity_id": entity_id},
            blocking=True,
        )
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == STATE_OFF

        assert await hass.config_entries.async_unload(entry.entry_id)


async def test_removed_light_cancels_active_and_rejects_queued_transition(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
) -> None:
    """A final zero cancels the active fade and no queued fade can revive it."""
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
        light_id = registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.entry_id}_{DIMMER_ID}"
        )
        number_id = registry.async_get_entity_id(
            "number", DOMAIN, f"{entry.entry_id}_{RAW_ID}"
        )
        assert light_id is not None
        assert number_id is not None
        entity = next(
            platform.entities[light_id]
            for platform in ep.async_get_platforms(hass, DOMAIN)
            if light_id in platform.entities
        )
        assert isinstance(entity, UsbDmxDimmer)

        await hass.services.async_call(
            "light",
            "turn_on",
            {ATTR_BRIGHTNESS: 128, ATTR_TRANSITION: 60},
            target={"entity_id": light_id},
            blocking=True,
        )
        assert 1 in controller._transition_tasks
        assert not controller._transition_tasks[1].done()

        service_gate = ObservedSemaphore(0, observe_acquire=1)
        entity.parallel_updates = service_gate
        queued = asyncio.create_task(
            hass.services.async_call(
                "light",
                "turn_on",
                {ATTR_BRIGHTNESS: 255, ATTR_TRANSITION: 0.05},
                target={"entity_id": light_id},
                blocking=True,
            )
        )
        await service_gate.acquire_started.wait()

        backend.send_gate = asyncio.Event()
        backend.send_started.clear()
        assert hass.config_entries.async_remove_subentry(entry, dimmer.subentry_id)
        await backend.send_started.wait()
        assert controller.current_frame[0] == 0
        service_gate.release()
        await service_gate.acquire_finished.wait()
        backend.send_gate.set()

        await queued
        await hass.async_block_till_done()
        settled = asyncio.Event()
        hass.loop.call_later(0.1, settled.set)
        await settled.wait()

        assert controller.current_frame[0] == 0
        assert backend.frames[-1][0] == 0
        assert 1 not in controller._transition_tasks
        assert hass.states.get(light_id) is None
        assert registry.async_get(light_id) is None
        assert hass.states.get(number_id) is not None
        assert registry.async_get(number_id) is not None
        assert len(controller._availability_listeners) == 1

        frames_after_removal = list(backend.frames)
        await entity.async_turn_off()
        assert backend.frames == frames_after_removal
        assert hass.states.get(light_id) is None

        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize(
    ("startup_behavior", "restored", "expected_values", "expected_state"),
    [
        (
            StartupBehavior.ZERO,
            State(
                "light.usb_dmx_interface_front",
                STATE_ON,
                {ATTR_BRIGHTNESS: 128},
            ),
            [0],
            STATE_OFF,
        ),
        (StartupBehavior.RESTORE, None, [0], STATE_OFF),
        (
            StartupBehavior.RESTORE,
            State(
                "light.usb_dmx_interface_front",
                STATE_OFF,
                {ATTR_BRIGHTNESS: 77},
            ),
            [0],
            STATE_OFF,
        ),
        (
            StartupBehavior.RESTORE,
            State(
                "light.usb_dmx_interface_front",
                STATE_ON,
                {ATTR_BRIGHTNESS: "invalid"},
            ),
            [0],
            STATE_OFF,
        ),
        (
            StartupBehavior.RESTORE,
            State(
                "light.usb_dmx_interface_front",
                STATE_ON,
                {ATTR_BRIGHTNESS: 128},
            ),
            [0, 128],
            STATE_ON,
        ),
    ],
)
async def test_startup_sends_zero_before_optional_restore(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    *,
    startup_behavior: StartupBehavior,
    restored: State | None,
    expected_values: list[int],
    expected_state: str,
) -> None:
    """Physical output is zeroed before any optional valid restore write."""
    fixture = make_fixture_subentry(fixture_id=DIMMER_ID, name="Front", address=1)
    entry = make_usb_dmx_entry(fixture, startup_behavior=startup_behavior)
    entry.add_to_hass(hass)
    backend = FakeBackend()
    mock_restore_cache(hass, [restored] if restored is not None else [])

    with (
        patch(
            "custom_components.usb_dmx.async_create_backend",
            AsyncMock(return_value=backend),
        ),
        patch("custom_components.usb_dmx.PLATFORMS", (Platform.LIGHT,)),
    ):
        assert await hass.config_entries.async_setup(entry.entry_id)
        registry = er.async_get(hass)
        entity_id = registry.async_get_entity_id(
            "light", DOMAIN, f"{entry.entry_id}_{DIMMER_ID}"
        )
        assert entity_id is not None

        assert [frame[0] for frame in backend.frames] == expected_values
        assert all(frame[1:] == bytes(511) for frame in backend.frames)
        state = hass.states.get(entity_id)
        assert state is not None
        assert state.state == expected_state

        assert await hass.config_entries.async_unload(entry.entry_id)


@pytest.mark.parametrize("mismatch", [False, True])
async def test_platform_rejects_malformed_fixture_identity(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    *,
    mismatch: bool,
) -> None:
    """Bad storage or identity fails setup instead of creating an entity."""
    stored_id = "not-a-uuid" if not mismatch else str(uuid4())
    unique_id = str(uuid4()) if mismatch else stored_id
    stored = make_fixture_subentry(fixture_id=stored_id, unique_id=unique_id)
    entry = make_usb_dmx_entry(stored)
    await _runtime_entry(entry)
    add_entities = MagicMock()

    with pytest.raises(FixtureValidationError, match="invalid_fixture"):
        await async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()
    await entry.runtime_data.controller.async_stop()


@pytest.mark.parametrize(
    ("first", "second", "reason"),
    [
        (
            {"fixture_id": DIMMER_ID, "name": "Front", "address": 1},
            {
                "fixture_id": RAW_ID,
                "fixture_type": "raw",
                "name": "Relay",
                "address": 1,
            },
            "duplicate_address",
        ),
        (
            {"fixture_id": DIMMER_ID, "name": "Front", "address": 1},
            {
                "fixture_id": RAW_ID,
                "fixture_type": "raw",
                "name": " front ",
                "address": 2,
            },
            "duplicate_name",
        ),
        (
            {"fixture_id": DIMMER_ID, "name": "Front", "address": 1},
            {
                "fixture_id": DIMMER_ID,
                "fixture_type": "raw",
                "name": "Relay",
                "address": 2,
            },
            "invalid_fixture",
        ),
    ],
)
async def test_light_platform_validates_complete_fixture_collection_before_filter(
    hass: HomeAssistant,
    make_fixture_subentry: Callable[..., ConfigSubentry],
    make_usb_dmx_entry: Callable[..., MockConfigEntry],
    *,
    first: dict[str, Any],
    second: dict[str, Any],
    reason: str,
) -> None:
    """Corrupt sibling collisions abort setup before any light is added."""
    stored_first = make_fixture_subentry(**first)
    stored_second = make_fixture_subentry(**second)
    entry = make_usb_dmx_entry(stored_first, stored_second)
    entry.add_to_hass(hass)
    await _runtime_entry(entry)
    add_entities = MagicMock()

    with pytest.raises(FixtureValidationError, match=reason):
        await async_setup_entry(hass, entry, add_entities)

    add_entities.assert_not_called()
    await entry.runtime_data.controller.async_stop()
