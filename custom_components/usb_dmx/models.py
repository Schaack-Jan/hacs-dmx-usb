"""Domain models for the USB DMX integration."""

from __future__ import annotations

from collections.abc import Collection, Mapping
from dataclasses import dataclass
from enum import StrEnum
from math import isfinite
from uuid import UUID

from .const import (
    CONF_FADE_SOURCE,
    CONF_FADE_TIME,
    CONF_FIXTURE_ADDRESS,
    CONF_FIXTURE_ID,
    CONF_FIXTURE_MAXIMUM,
    CONF_FIXTURE_MINIMUM,
    CONF_FIXTURE_NAME,
    CONF_FIXTURE_TYPE,
    CONF_PIXEL_COUNT,
    CONF_PIXEL_MODE,
    DMX_CHANNEL_COUNT,
    DMX_MAX_VALUE,
    FADE_TIME_MAX,
    LED_BAR_MAX_PIXELS,
)

_UUID_VERSION = 4


class FixtureType(StrEnum):
    """Supported fixture types."""

    DIMMER = "dimmer"
    RAW = "raw"
    RGB = "rgb"
    RGBW = "rgbw"
    RGBWAUV = "rgbwauv"
    DIMMER_RGB = "dimmer_rgb"
    DIMMER_RGBW_STROBE = "dimmer_rgbw_strobe"
    LED_BAR = "led_bar"


class PixelMode(StrEnum):
    """Pixel channel order for directly-addressable LED bars."""

    RGB = "rgb"
    RGBW = "rgbw"


class FadeSource(StrEnum):
    """Supported sources of a dimmer fade time."""

    GLOBAL = "global"
    INDIVIDUAL = "individual"


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


def validate_fade_time(value: object) -> float:
    """Validate and normalize a finite fade setting to hundredths of a second."""
    if (
        type(value) not in (int, float)
        or not 0 <= value <= FADE_TIME_MAX
        or not isfinite(value)
    ):
        raise FixtureValidationError("invalid_fade_time")
    return round(float(value), 2)


def validate_fade_source(value: object) -> FadeSource:
    """Validate one exact persisted fade source."""
    if not isinstance(value, str):
        raise FixtureValidationError("invalid_fade_source")
    try:
        return FadeSource(value)
    except ValueError as err:
        raise FixtureValidationError("invalid_fade_source") from err


@dataclass(frozen=True, slots=True)
class FixtureConfig:
    """Stored configuration for one DMX fixture and its channel footprint."""

    fixture_id: str
    fixture_type: FixtureType
    name: str
    address: int
    minimum: int
    maximum: int
    fade_time: float = 0.0
    fade_source: FadeSource = FadeSource.GLOBAL
    pixel_count: int = 1
    pixel_mode: PixelMode = PixelMode.RGB

    @property
    def channel_count(self) -> int:
        """Return the number of consecutive DMX slots consumed by the fixture."""
        if self.fixture_type is FixtureType.LED_BAR:
            return self.pixel_count * (3 if self.pixel_mode is PixelMode.RGB else 4)
        return {
            FixtureType.DIMMER: 1,
            FixtureType.RAW: 1,
            FixtureType.RGB: 3,
            FixtureType.RGBW: 4,
            FixtureType.RGBWAUV: 6,
            FixtureType.DIMMER_RGB: 4,
            FixtureType.DIMMER_RGBW_STROBE: 6,
        }[self.fixture_type]

    @property
    def occupied_addresses(self) -> range:
        """Return all one-based addresses reserved by this fixture."""
        return range(self.address, self.address + self.channel_count)

    @classmethod
    def from_mapping(cls, data: Mapping[str, object]) -> FixtureConfig:
        """Create and validate a fixture from its JSON-safe representation."""
        pixel_count = data.get(CONF_PIXEL_COUNT, 1)
        pixel_mode = data.get(CONF_PIXEL_MODE, PixelMode.RGB)
        if type(pixel_count) is not int or not 1 <= pixel_count <= LED_BAR_MAX_PIXELS:
            raise FixtureValidationError("invalid_pixel_count")
        try:
            if not isinstance(pixel_mode, str):
                raise TypeError
            pixel_mode = PixelMode(pixel_mode)
        except (TypeError, ValueError) as err:
            raise FixtureValidationError("invalid_pixel_mode") from err
        fade_time = validate_fade_time(data.get(CONF_FADE_TIME, 0.0))
        fade_source = validate_fade_source(
            data.get(CONF_FADE_SOURCE, FadeSource.GLOBAL)
        )
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
                fade_time=fade_time,
                fade_source=fade_source,
                pixel_count=pixel_count,
                pixel_mode=pixel_mode,
            )
        except (KeyError, TypeError, ValueError) as err:
            raise FixtureValidationError("invalid_fixture") from err

        validate_fixtures([fixture])
        return fixture

    def as_mapping(self) -> dict[str, object]:
        """Return the JSON-safe storage representation of this fixture."""
        data: dict[str, object] = {
            CONF_FIXTURE_ID: self.fixture_id,
            CONF_FIXTURE_TYPE: self.fixture_type.value,
            CONF_FIXTURE_NAME: self.name,
            CONF_FIXTURE_ADDRESS: self.address,
            CONF_FIXTURE_MINIMUM: self.minimum,
            CONF_FIXTURE_MAXIMUM: self.maximum,
        }
        if self.fixture_type is FixtureType.LED_BAR:
            data[CONF_PIXEL_COUNT] = self.pixel_count
            data[CONF_PIXEL_MODE] = self.pixel_mode.value
        if self.fixture_type is FixtureType.DIMMER:
            data[CONF_FADE_TIME] = self.fade_time
            data[CONF_FADE_SOURCE] = self.fade_source.value
        return data


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
            or not 1 <= fixture.address
            or fixture.address + fixture.channel_count - 1 > DMX_CHANNEL_COUNT
        ):
            raise FixtureValidationError("invalid_address")

        if (
            type(fixture.minimum) is not int
            or type(fixture.maximum) is not int
            or not 0 <= fixture.minimum <= fixture.maximum <= DMX_MAX_VALUE
        ):
            raise FixtureValidationError("invalid_range")

        if (
            type(fixture.pixel_count) is not int
            or not 1 <= fixture.pixel_count <= LED_BAR_MAX_PIXELS
        ):
            raise FixtureValidationError("invalid_pixel_count")
        if not isinstance(fixture.pixel_mode, PixelMode):
            raise FixtureValidationError("invalid_pixel_mode")

        validate_fade_time(fixture.fade_time)
        validate_fade_source(fixture.fade_source)

        if any(address in addresses for address in fixture.occupied_addresses):
            raise FixtureValidationError("duplicate_address")
        normalized_name = fixture.name.strip().casefold()
        if normalized_name in names:
            raise FixtureValidationError("duplicate_name")
        if fixture.fixture_id in fixture_ids:
            raise FixtureValidationError("invalid_fixture")

        addresses.update(fixture.occupied_addresses)
        fixture_ids.add(fixture.fixture_id)
        names.add(normalized_name)
