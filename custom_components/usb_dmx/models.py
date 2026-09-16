"""Domain models for the USB DMX integration."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from uuid import UUID

from .const import (
    CONF_FIXTURE_ADDRESS,
    CONF_FIXTURE_ID,
    CONF_FIXTURE_MAXIMUM,
    CONF_FIXTURE_MINIMUM,
    CONF_FIXTURE_NAME,
    CONF_FIXTURE_TYPE,
    DMX_CHANNEL_COUNT,
    DMX_MAX_VALUE,
)

_UUID_VERSION = 4


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
            fixture_id = data[CONF_FIXTURE_ID]
            fixture_type = data[CONF_FIXTURE_TYPE]
            name = data[CONF_FIXTURE_NAME]
            address = data[CONF_FIXTURE_ADDRESS]
            minimum = data[CONF_FIXTURE_MINIMUM]
            maximum = data[CONF_FIXTURE_MAXIMUM]
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
            CONF_FIXTURE_ID: self.fixture_id,
            CONF_FIXTURE_TYPE: self.fixture_type.value,
            CONF_FIXTURE_NAME: self.name,
            CONF_FIXTURE_ADDRESS: self.address,
            CONF_FIXTURE_MINIMUM: self.minimum,
            CONF_FIXTURE_MAXIMUM: self.maximum,
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


def _is_canonical_uuid4(value: object) -> bool:
    """Return whether a value is a canonical UUID4 string."""
    if not isinstance(value, str):
        return False

    try:
        parsed = UUID(value)
    except ValueError:
        return False
    return parsed.version == _UUID_VERSION and str(parsed) == value


def validate_fixtures(fixtures: Collection[FixtureConfig]) -> None:
    """Validate a complete fixture collection."""
    addresses: set[int] = set()
    fixture_ids: set[str] = set()
    names: set[str] = set()

    for fixture in fixtures:
        if (
            not isinstance(fixture, FixtureConfig)
            or not _is_canonical_uuid4(fixture.fixture_id)
            or not isinstance(fixture.fixture_type, FixtureType)
            or not fixture.name.strip()
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
        normalized_name = fixture.name.strip().casefold()
        if normalized_name in names:
            raise FixtureValidationError("duplicate_name")
        if fixture.fixture_id in fixture_ids:
            raise FixtureValidationError("invalid_fixture")

        addresses.add(fixture.address)
        fixture_ids.add(fixture.fixture_id)
        names.add(normalized_name)
