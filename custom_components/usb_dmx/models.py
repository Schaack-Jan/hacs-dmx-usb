"""Domain models for the USB DMX integration."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from typing import Final

from .const import DMX_CHANNEL_COUNT, DMX_MAX_VALUE

_FIXTURE_ID: Final = "id"
_FIXTURE_TYPE: Final = "type"
_FIXTURE_NAME: Final = "name"
_FIXTURE_ADDRESS: Final = "address"
_FIXTURE_MINIMUM: Final = "minimum"
_FIXTURE_MAXIMUM: Final = "maximum"


class FixtureType(StrEnum):
    """Supported fixture types."""

    DIMMER = "dimmer"
    RAW = "raw"


class StartupBehavior(StrEnum):
    """Supported startup behaviors."""

    RESTORE = "restore"
    ZERO = "zero"


class FixtureValidationError(ValueError):
    """Report fixture validation failures using a stable reason code."""

    def __init__(self, reason: str) -> None:
        """Initialize an error with its stable reason code."""
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True, slots=True)
class FixtureConfig:
    """Stored configuration for one single-slot DMX fixture."""

    fixture_id: str
    fixture_type: FixtureType
    name: str
    address: int
    minimum: int
    maximum: int

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> FixtureConfig:
        """Create and validate a fixture from its JSON-safe representation."""
        try:
            fixture_id = data[_FIXTURE_ID]
            fixture_type = data[_FIXTURE_TYPE]
            name = data[_FIXTURE_NAME]
            address = data[_FIXTURE_ADDRESS]
            minimum = data[_FIXTURE_MINIMUM]
            maximum = data[_FIXTURE_MAXIMUM]
            if not isinstance(fixture_id, str) or not isinstance(name, str):
                raise TypeError
            if not isinstance(fixture_type, str):
                raise TypeError
            if type(address) is not int or type(minimum) is not int:
                raise TypeError
            if type(maximum) is not int:
                raise TypeError
            fixture = cls(
                fixture_id=fixture_id,
                fixture_type=FixtureType(fixture_type),
                name=name,
                address=address,
                minimum=minimum,
                maximum=maximum,
            )
        except (KeyError, TypeError, ValueError) as err:
            raise FixtureValidationError("invalid_fixture") from err

        validate_fixtures([fixture])
        return fixture

    def as_mapping(self) -> dict[str, object]:
        """Return the JSON-safe storage representation of this fixture."""
        return {
            _FIXTURE_ID: self.fixture_id,
            _FIXTURE_TYPE: self.fixture_type.value,
            _FIXTURE_NAME: self.name,
            _FIXTURE_ADDRESS: self.address,
            _FIXTURE_MINIMUM: self.minimum,
            _FIXTURE_MAXIMUM: self.maximum,
        }


@dataclass(frozen=True, slots=True)
class BackendCapabilities:
    """Immutable capabilities advertised by a DMX backend."""

    refresh_mode: str
    channel_count: int = DMX_CHANNEL_COUNT


@dataclass(frozen=True, slots=True)
class BackendInfo:
    """Immutable identifying information returned when probing a backend."""

    name: str
    device: str


def validate_fixtures(fixtures: Collection[FixtureConfig]) -> None:
    """Validate a complete fixture collection."""
    addresses: set[int] = set()
    names: set[str] = set()

    for fixture in fixtures:
        if (
            not isinstance(fixture, FixtureConfig)
            or not fixture.fixture_id
            or not isinstance(fixture.fixture_type, FixtureType)
            or not fixture.name
        ):
            raise FixtureValidationError("invalid_fixture")

        if (
            type(fixture.address) is not int
            or not 1 <= fixture.address <= DMX_CHANNEL_COUNT
        ):
            raise FixtureValidationError("invalid_address")

        if (
            type(fixture.minimum) is not int
            or type(fixture.maximum) is not int
            or not 0 <= fixture.minimum <= fixture.maximum <= DMX_MAX_VALUE
        ):
            raise FixtureValidationError("invalid_range")

        if fixture.address in addresses:
            raise FixtureValidationError("duplicate_address")
        if fixture.name in names:
            raise FixtureValidationError("duplicate_name")

        addresses.add(fixture.address)
        names.add(fixture.name)
