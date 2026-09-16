"""Shared entity support for USB DMX fixtures."""

from __future__ import annotations

from typing import TYPE_CHECKING, override

from homeassistant.core import callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.device_registry import DeviceInfo
from homeassistant.helpers.entity import Entity

from . import UsbDmxConfigEntry
from .const import CONF_DEVICE, DOMAIN
from .controller import DmxController
from .models import FixtureConfig, FixtureValidationError

if TYPE_CHECKING:
    from homeassistant.config_entries import ConfigSubentry
    from homeassistant.core import HomeAssistant


def fixture_from_subentry(subentry: ConfigSubentry) -> FixtureConfig:
    """Load one fixture while enforcing its immutable subentry identity."""
    fixture = FixtureConfig.from_mapping(subentry.data)
    if subentry.unique_id != fixture.fixture_id:
        raise FixtureValidationError("invalid_fixture")
    return fixture


def _interface_name(entry: UsbDmxConfigEntry) -> str:
    """Return a useful device name without leaking a raw serial path."""
    title = entry.title.strip()
    device = entry.data.get(CONF_DEVICE)
    if title and (not isinstance(device, str) or device not in title):
        return title
    return "USB DMX Interface"


def interface_device_info(entry: UsbDmxConfigEntry) -> DeviceInfo:
    """Return stable parent-owned metadata for the physical interface."""
    return DeviceInfo(
        identifiers={(DOMAIN, entry.entry_id)},
        manufacturer="USB DMX",
        model="DMX512 Interface",
        name=_interface_name(entry),
    )


def get_interface_device(
    hass: HomeAssistant, entry: UsbDmxConfigEntry
) -> dr.DeviceEntry:
    """Get one parent-owned device shared by every fixture subentry."""
    return dr.async_get(hass).async_get_or_create(
        config_entry_id=entry.entry_id,
        config_subentry_id=None,
        **interface_device_info(entry),
    )


class UsbDmxEntity(Entity):
    """Base entity for one fixture on a shared physical interface."""

    _attr_should_poll = False

    def __init__(self, fixture: FixtureConfig, entry: UsbDmxConfigEntry) -> None:
        """Initialize stable identity and shared runtime ownership."""
        self.fixture = fixture
        self.entry = entry
        self.controller: DmxController = entry.runtime_data.controller
        self._attr_name = fixture.name
        self._attr_unique_id = f"{entry.entry_id}_{fixture.fixture_id}"

    @property
    @override
    def available(self) -> bool:
        """Mirror the shared controller's transport connectivity."""
        return self.controller.available

    @override
    async def async_added_to_hass(self) -> None:
        """Subscribe once to shared availability changes."""
        await super().async_added_to_hass()
        self.async_on_remove(
            self.controller.add_availability_listener(
                self._async_handle_availability_update
            )
        )

    @callback
    def _async_handle_availability_update(
        self,
        _available: bool,  # noqa: FBT001
    ) -> None:
        """Publish controller availability when the entity is fully added."""
        if self.hass is not None and self.platform is not None:
            self.async_write_ha_state()
