"""Persist and publish current interface and fixture fade settings."""

from __future__ import annotations

import asyncio
from collections.abc import Callable

from homeassistant.config_entries import ConfigEntry, ConfigSubentry, UnknownSubEntry
from homeassistant.core import HomeAssistant, callback

from .const import (
    CONF_FADE_SOURCE,
    CONF_FADE_TIME,
    CONF_GLOBAL_FADE_TIME,
    SUBENTRY_TYPE_FIXTURE,
)
from .models import FadeSource, validate_fade_source, validate_fade_time


class FadeSettings:
    """Coordinate storage merges without writing DMX or reloading the interface."""

    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        """Keep the current config entry and a common setter lock."""
        self._hass = hass
        self._entry = entry
        self._lock = asyncio.Lock()
        self._listeners: list[Callable[[], None]] = []

    @property
    def global_fade_time(self) -> float:
        """Read and validate the current interface fade time."""
        return validate_fade_time(self._entry.options.get(CONF_GLOBAL_FADE_TIME, 0.0))

    def _fixture_subentry(self, fixture_id: str) -> ConfigSubentry:
        """Find a currently existing fixture by its immutable UUID."""
        for subentry in self._entry.subentries.values():
            if (
                subentry.subentry_type == SUBENTRY_TYPE_FIXTURE
                and subentry.unique_id == fixture_id
            ):
                return subentry
        raise UnknownSubEntry(fixture_id)

    @callback
    def fixture_settings(self, fixture_id: str) -> tuple[float, FadeSource]:
        """Read current fixture settings with backwards-compatible defaults."""
        data = self._fixture_subentry(fixture_id).data
        return (
            validate_fade_time(data.get(CONF_FADE_TIME, 0.0)),
            validate_fade_source(data.get(CONF_FADE_SOURCE, FadeSource.GLOBAL)),
        )

    @callback
    def add_listener(self, callback: Callable[[], None]) -> Callable[[], None]:
        """Subscribe to successful settings updates and return an unsubscribe."""
        self._listeners.append(callback)

        def unsubscribe() -> None:
            if callback in self._listeners:
                self._listeners.remove(callback)

        return unsubscribe

    @callback
    def _publish(self) -> None:
        """Publish after storage changes are visible to every subscriber."""
        for listener in tuple(self._listeners):
            listener()

    async def async_set_global_fade_time(self, value: object) -> None:
        """Persist a validated interface time while retaining current options."""
        fade_time = validate_fade_time(value)
        async with self._lock:
            self._hass.config_entries.async_update_entry(
                self._entry,
                options=dict(self._entry.options) | {CONF_GLOBAL_FADE_TIME: fade_time},
            )
            self._publish()

    async def _async_set_fixture_setting(
        self, fixture_id: str, key: str, value: float | str
    ) -> None:
        """Recheck fixture existence under the lock before merging current data."""
        async with self._lock:
            subentry = self._fixture_subentry(fixture_id)
            self._hass.config_entries.async_update_subentry(
                self._entry, subentry, data=dict(subentry.data) | {key: value}
            )
            self._publish()

    async def async_set_fixture_fade_time(self, fixture_id: str, value: object) -> None:
        """Persist a validated individual fade time without changing its source."""
        await self._async_set_fixture_setting(
            fixture_id, CONF_FADE_TIME, validate_fade_time(value)
        )

    async def async_set_fixture_fade_source(
        self, fixture_id: str, value: object
    ) -> None:
        """Persist an exact source without changing the individual fade time."""
        await self._async_set_fixture_setting(
            fixture_id, CONF_FADE_SOURCE, validate_fade_source(value).value
        )
