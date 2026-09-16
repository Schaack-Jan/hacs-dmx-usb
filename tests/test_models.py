"""Tests for USB DMX domain models and backend contracts."""

from __future__ import annotations

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from custom_components.usb_dmx.backends.base import DmxBackend
from custom_components.usb_dmx.models import (
    BackendCapabilities,
    BackendInfo,
    FixtureConfig,
    FixtureType,
    FixtureValidationError,
    StartupBehavior,
    validate_fixtures,
)


def _fixture(
    *,
    fixture_id: str = "b95fb1d3-d613-4848-a99c-cdbeb31f2842",
    fixture_type: FixtureType = FixtureType.DIMMER,
    name: str = "Front light",
    address: int = 1,
    minimum: int = 0,
    maximum: int = 255,
) -> FixtureConfig:
    return FixtureConfig(
        fixture_id=fixture_id,
        fixture_type=fixture_type,
        name=name,
        address=address,
        minimum=minimum,
        maximum=maximum,
    )


@pytest.mark.parametrize("address", [1, 512])
def test_validate_fixtures_accepts_boundary_addresses(address: int) -> None:
    """DMX slots at both ends of the universe are valid."""
    validate_fixtures([_fixture(address=address)])


@pytest.mark.parametrize("address", [0, 513])
def test_validate_fixtures_rejects_out_of_range_addresses(address: int) -> None:
    """An off-by-one address outside the universe is rejected."""
    with pytest.raises(FixtureValidationError, match="^invalid_address$") as error:
        validate_fixtures([_fixture(address=address)])

    assert error.value.reason == "invalid_address"


@pytest.mark.parametrize(
    ("minimum", "maximum"),
    [(-1, 255), (0, 256), (128, 127)],
)
def test_validate_fixtures_rejects_invalid_value_ranges(
    minimum: int, maximum: int
) -> None:
    """Fixture value ranges stay within one byte and remain ordered."""
    with pytest.raises(FixtureValidationError, match="^invalid_range$") as error:
        validate_fixtures([_fixture(minimum=minimum, maximum=maximum)])

    assert error.value.reason == "invalid_range"


def test_fixture_mapping_round_trip_preserves_stored_values() -> None:
    """The JSON-safe storage boundary does not lose fixture identity or type."""
    stored = {
        "id": "8091f8d2-16fd-4da8-b663-943b52fcde41",
        "type": "raw",
        "name": "Fog level",
        "address": 512,
        "minimum": 17,
        "maximum": 231,
    }

    fixture = FixtureConfig.from_mapping(stored)

    assert fixture == FixtureConfig(
        fixture_id="8091f8d2-16fd-4da8-b663-943b52fcde41",
        fixture_type=FixtureType.RAW,
        name="Fog level",
        address=512,
        minimum=17,
        maximum=231,
    )
    assert fixture.as_mapping() == stored


def test_validate_fixtures_rejects_duplicate_addresses() -> None:
    """Two single-slot fixtures cannot overlap the same DMX address."""
    fixtures = [
        _fixture(),
        _fixture(
            fixture_id="1d7e031e-8d74-4a17-97c8-e76b4d708006",
            name="Rear light",
        ),
    ]

    with pytest.raises(FixtureValidationError, match="^duplicate_address$") as error:
        validate_fixtures(fixtures)

    assert error.value.reason == "duplicate_address"


def test_validate_fixtures_rejects_duplicate_names() -> None:
    """Fixture names must identify one fixture within an entry."""
    fixtures = [
        _fixture(),
        _fixture(
            fixture_id="1d7e031e-8d74-4a17-97c8-e76b4d708006",
            address=2,
        ),
    ]

    with pytest.raises(FixtureValidationError, match="^duplicate_name$") as error:
        validate_fixtures(fixtures)

    assert error.value.reason == "duplicate_name"


def test_fixture_from_mapping_rejects_unknown_fixture_type() -> None:
    """Unknown stored fixture types fail with the stable invalid-fixture code."""
    stored = {
        "id": "8091f8d2-16fd-4da8-b663-943b52fcde41",
        "type": "rgb",
        "name": "Unsupported fixture",
        "address": 1,
        "minimum": 0,
        "maximum": 255,
    }

    with pytest.raises(FixtureValidationError, match="^invalid_fixture$") as error:
        FixtureConfig.from_mapping(stored)

    assert error.value.reason == "invalid_fixture"


def test_domain_models_are_frozen_and_slotted() -> None:
    """Runtime code cannot mutate immutable model state or add attributes."""
    fixture = _fixture()
    capabilities = BackendCapabilities(refresh_mode="hardware")
    info = BackendInfo(name="serial_pro", device="/dev/ttyUSB0")

    with pytest.raises(FrozenInstanceError):
        fixture.name = "Changed"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        capabilities.refresh_mode = "software"  # type: ignore[misc]
    with pytest.raises(FrozenInstanceError):
        info.device = "/dev/ttyUSB1"  # type: ignore[misc]
    assert not hasattr(fixture, "__dict__")


def test_enum_storage_values_are_stable() -> None:
    """Persisted enum values match the config and options schema."""
    assert [member.value for member in FixtureType] == ["dimmer", "raw"]
    assert [member.value for member in StartupBehavior] == ["restore", "zero"]


class _RecordingBackend(DmxBackend):
    """Concrete backend used to exercise the common backend contract."""

    capabilities = BackendCapabilities(refresh_mode="hardware")

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def connect(self) -> None:
        self.calls.append("connect")

    async def disconnect(self) -> None:
        self.calls.append("disconnect")

    async def send_frame(self, frame: bytes) -> None:
        self._validate_frame(frame)
        self.calls.append("send_frame")

    async def probe(self) -> BackendInfo:
        return BackendInfo(name="recording", device="memory://")


def test_backend_reconnect_disconnects_before_connecting() -> None:
    """The default reconnect sequence replaces a possibly broken handle."""
    backend = _RecordingBackend()

    asyncio.run(backend.reconnect())

    assert backend.calls == ["disconnect", "connect"]


@pytest.mark.parametrize("length", [0, 511, 513])
def test_backend_rejects_frames_that_are_not_one_universe(length: int) -> None:
    """A backend never accepts a partial or oversized DMX universe."""
    backend = _RecordingBackend()

    with pytest.raises(ValueError, match="exactly 512 bytes"):
        asyncio.run(backend.send_frame(bytes(length)))


def test_backend_accepts_exactly_one_universe() -> None:
    """A complete 512-byte DMX frame crosses the backend boundary."""
    backend = _RecordingBackend()

    asyncio.run(backend.send_frame(bytes(512)))

    assert backend.calls == ["send_frame"]
