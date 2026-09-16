"""Config flow for USB DMX interfaces."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, Protocol, override

import voluptuous as vol
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers.selector import (
    SelectSelector,
    SelectSelectorConfig,
    SelectSelectorMode,
)

from . import async_create_backend
from .backends.base import BackendError
from .const import (
    BACKEND_SERIAL_PRO,
    CONF_BACKEND,
    CONF_DEVICE,
    CONF_INTERFACE_ID,
    DOMAIN,
)

if TYPE_CHECKING:
    from homeassistant.core import HomeAssistant
    from homeassistant.helpers.service_info.usb import UsbServiceInfo

_MANUAL_PATH = "__manual_path__"
_FLOW_DEVICE_PATH_KEY = "usb_dmx_device_path_key"
_FLOW_INTERFACE_ID = "usb_dmx_interface_id"


class _SerialDevice(Protocol):
    """Describe USB serial-port metadata used by this flow."""

    device: str
    serial_number: str | None
    manufacturer: str | None
    description: str | None


@dataclass(frozen=True, slots=True)
class _ValidationEntry:
    """Supply transient connection data to the shared backend factory."""

    data: Mapping[str, Any]


async def _async_scan_serial_ports(hass: HomeAssistant) -> Sequence[_SerialDevice]:
    """Return serial candidates from Home Assistant's USB integration."""
    from homeassistant.components import usb  # noqa: PLC0415

    return await usb.async_scan_serial_ports(hass)


async def _async_get_stable_path(hass: HomeAssistant, path: str) -> str:
    """Prefer Home Assistant's stable /dev/serial/by-id path when available."""
    from homeassistant.components import usb  # noqa: PLC0415

    return await hass.async_add_executor_job(usb.get_serial_by_id, path)


def _normalize_path(path: str) -> str:
    """Normalize a non-empty serial path without resolving stable symlinks."""
    normalized = os.path.normpath(path.strip())
    if normalized.startswith("//"):
        return f"/{normalized.lstrip('/')}"
    return normalized


async def _async_get_path_comparison_key(hass: HomeAssistant, path: str) -> str:
    """Return a canonical device key without changing the stored stable path."""
    real_path = await hass.async_add_executor_job(
        os.path.realpath, _normalize_path(path)
    )
    return _normalize_path(real_path)


def _interface_id(
    path: str,
    *,
    serial_number: str | None = None,
    manufacturer: str | None = None,
    product: str | None = None,
) -> str:
    """Build a stable product identity, falling back to the normalized path."""
    if serial_number and serial_number.strip():
        return ":".join(
            part.strip().casefold()
            for part in (manufacturer, product, serial_number)
            if part and part.strip()
        )
    return _normalize_path(path)


def _backend_selector(*, default: str = BACKEND_SERIAL_PRO) -> vol.Marker:
    """Return the explicit V1 protocol-backend selector field."""
    return vol.Required(CONF_BACKEND, default=default)


class UsbDmxConfigFlow(ConfigFlow, domain=DOMAIN):
    """Configure one local DMX USB Pro protocol interface."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize per-flow candidate and discovery state."""
        self._candidates: dict[str, _SerialDevice] | None = None
        self._discovery_data: dict[str, str] | None = None

    async def _async_load_candidates(self) -> None:
        """Load selector candidates without opening their serial transports."""
        if self._candidates is not None:
            return
        self._candidates = {}
        for candidate in await _async_scan_serial_ports(self.hass):
            path = await _async_get_stable_path(self.hass, candidate.device)
            self._candidates[_normalize_path(path)] = candidate

    async def _async_validate_connection(self, data: Mapping[str, str]) -> bool:
        """Open and always close one short-lived validation backend."""
        backend = await async_create_backend(_ValidationEntry(data=data))
        valid = False
        try:
            try:
                await backend.connect()
            except BackendError:
                pass
            else:
                valid = True
        finally:
            try:
                await backend.disconnect()
            except BackendError:
                valid = False
        return valid

    def _is_interface_id_configured(self, interface_id: str) -> bool:
        """Return whether this domain already owns an interface identity."""
        return (
            self.hass.config_entries.async_entry_for_domain_unique_id(
                DOMAIN, interface_id
            )
            is not None
        )

    async def _async_is_device_configured(
        self, path: str, *, exclude_entry_id: str | None = None
    ) -> bool:
        """Return whether another domain entry owns the canonical device path."""
        comparison_key = await _async_get_path_comparison_key(self.hass, path)
        for entry in self.hass.config_entries.async_entries(DOMAIN):
            if entry.entry_id == exclude_entry_id:
                continue
            configured_path = entry.data.get(CONF_DEVICE)
            if not isinstance(configured_path, str):
                continue
            if (
                await _async_get_path_comparison_key(self.hass, configured_path)
                == comparison_key
            ):
                return True
        return False

    async def _async_interface_is_configured(
        self, *, path: str, interface_id: str
    ) -> bool:
        """Check both HA identity and canonical device path before opening."""
        return self._is_interface_id_configured(
            interface_id
        ) or await self._async_is_device_configured(path)

    def _release_flow_identity(self) -> None:
        """Release transient identity reservations after retryable errors."""
        self.context.pop(_FLOW_DEVICE_PATH_KEY, None)
        self.context.pop(_FLOW_INTERFACE_ID, None)

    async def _async_reserve_flow_identity(
        self, *, path: str, interface_id: str | None
    ) -> bool:
        """Reserve a canonical device and optional product ID for this flow."""
        try:
            comparison_key = await _async_get_path_comparison_key(self.hass, path)
        except BaseException:
            self._release_flow_identity()
            raise
        if self.context.get(_FLOW_DEVICE_PATH_KEY) == comparison_key and (
            interface_id is None or self.context.get(_FLOW_INTERFACE_ID) == interface_id
        ):
            return True

        self._release_flow_identity()
        if self._async_in_progress(
            include_uninitialized=True,
            match_context={_FLOW_DEVICE_PATH_KEY: comparison_key},
        ):
            return False
        if interface_id is not None and self._async_in_progress(
            include_uninitialized=True,
            match_context={_FLOW_INTERFACE_ID: interface_id},
        ):
            return False

        self.context[_FLOW_DEVICE_PATH_KEY] = comparison_key
        if interface_id is not None:
            self.context[_FLOW_INTERFACE_ID] = interface_id
        return True

    async def _async_create_interface_entry(
        self,
        *,
        backend: str,
        path: str,
        interface_id: str,
    ) -> ConfigFlowResult:
        """Reject duplicates, validate transport, and create an entry."""
        if await self._async_interface_is_configured(
            path=path, interface_id=interface_id
        ):
            return self.async_abort(reason="already_configured")
        if not await self._async_reserve_flow_identity(
            path=path, interface_id=interface_id
        ):
            return self.async_abort(reason="already_in_progress")
        try:
            if await self._async_interface_is_configured(
                path=path, interface_id=interface_id
            ):
                self._release_flow_identity()
                return self.async_abort(reason="already_configured")
            data = {
                CONF_BACKEND: backend,
                CONF_DEVICE: path,
                CONF_INTERFACE_ID: interface_id,
            }
            valid = await self._async_validate_connection(data)
            if not valid:
                self._release_flow_identity()
                return self.async_show_form(
                    step_id="manual",
                    data_schema=self._manual_schema(backend=backend, device=path),
                    errors={"base": "cannot_connect"},
                )
            if await self._async_interface_is_configured(
                path=path, interface_id=interface_id
            ):
                self._release_flow_identity()
                return self.async_abort(reason="already_configured")
            await self.async_set_unique_id(interface_id)
            self._abort_if_unique_id_configured()
            return self.async_create_entry(title=f"USB DMX ({path})", data=data)
        except BaseException:
            self._release_flow_identity()
            raise

    @staticmethod
    def _manual_schema(
        *, backend: str = BACKEND_SERIAL_PRO, device: str | None = None
    ) -> vol.Schema:
        """Return a manual raw-path schema with suggested current values."""
        device_field: vol.Marker = vol.Required(CONF_DEVICE)
        if device is not None:
            device_field = vol.Required(CONF_DEVICE, default=device)
        return vol.Schema(
            {
                _backend_selector(default=backend): SelectSelector(
                    SelectSelectorConfig(
                        options=[BACKEND_SERIAL_PRO],
                        mode=SelectSelectorMode.DROPDOWN,
                    )
                ),
                device_field: cv.string,
            }
        )

    @override
    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Select a discovered serial candidate or choose manual entry."""
        await self._async_load_candidates()
        candidates = self._candidates or {}

        if user_input is not None:
            backend = user_input[CONF_BACKEND]
            path = user_input[CONF_DEVICE]
            if path == _MANUAL_PATH:
                return await self.async_step_manual(
                    {CONF_BACKEND: backend} if backend != BACKEND_SERIAL_PRO else None
                )
            candidate = candidates[path]
            interface_id = _interface_id(
                path,
                serial_number=candidate.serial_number,
                manufacturer=candidate.manufacturer,
                product=candidate.description,
            )
            return await self._async_create_interface_entry(
                backend=backend, path=path, interface_id=interface_id
            )

        options = [*candidates, _MANUAL_PATH]
        return self.async_show_form(
            step_id="user",
            data_schema=vol.Schema(
                {
                    _backend_selector(): SelectSelector(
                        SelectSelectorConfig(
                            options=[BACKEND_SERIAL_PRO],
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                    vol.Required(CONF_DEVICE): SelectSelector(
                        SelectSelectorConfig(
                            options=options,
                            mode=SelectSelectorMode.DROPDOWN,
                        )
                    ),
                }
            ),
        )

    async def async_step_manual(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Accept a raw serial path as an escape hatch."""
        errors: dict[str, str] = {}
        if user_input is not None and CONF_DEVICE in user_input:
            raw_path = user_input[CONF_DEVICE]
            if not raw_path.strip():
                errors[CONF_DEVICE] = "invalid_device"
            else:
                normalized_path = _normalize_path(raw_path)
                path = _normalize_path(
                    await _async_get_stable_path(self.hass, normalized_path)
                )
                interface_id = _interface_id(path)
                return await self._async_create_interface_entry(
                    backend=user_input[CONF_BACKEND],
                    path=path,
                    interface_id=interface_id,
                )

        return self.async_show_form(
            step_id="manual",
            data_schema=self._manual_schema(),
            errors=errors,
        )

    @override
    async def async_step_usb(self, discovery_info: UsbServiceInfo) -> ConfigFlowResult:
        """Handle explicitly forwarded USB discovery data."""
        path = _normalize_path(
            await _async_get_stable_path(self.hass, discovery_info.device)
        )
        interface_id = _interface_id(
            path,
            serial_number=discovery_info.serial_number,
            manufacturer=discovery_info.manufacturer,
            product=discovery_info.description,
        )
        if await self._async_interface_is_configured(
            path=path, interface_id=interface_id
        ):
            return self.async_abort(reason="already_configured")
        if not await self._async_reserve_flow_identity(
            path=path, interface_id=interface_id
        ):
            return self.async_abort(reason="already_in_progress")
        try:
            if await self._async_interface_is_configured(
                path=path, interface_id=interface_id
            ):
                self._release_flow_identity()
                return self.async_abort(reason="already_configured")
            self._discovery_data = {
                CONF_BACKEND: BACKEND_SERIAL_PRO,
                CONF_DEVICE: path,
                CONF_INTERFACE_ID: interface_id,
            }
            return await self.async_step_usb_confirm()
        except BaseException:
            self._release_flow_identity()
            raise

    async def async_step_usb_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Require confirmation before validating and storing USB discovery."""
        if (discovery_data := self._discovery_data) is None:
            return self.async_abort(reason="invalid_discovery_info")
        errors: dict[str, str] = {}
        if user_input is not None:
            path = discovery_data[CONF_DEVICE]
            interface_id = discovery_data[CONF_INTERFACE_ID]
            if not await self._async_reserve_flow_identity(
                path=path, interface_id=interface_id
            ):
                return self.async_abort(reason="already_in_progress")
            try:
                if await self._async_interface_is_configured(
                    path=path, interface_id=interface_id
                ):
                    self._release_flow_identity()
                    return self.async_abort(reason="already_configured")
                valid = await self._async_validate_connection(discovery_data)
                if valid:
                    if await self._async_interface_is_configured(
                        path=path, interface_id=interface_id
                    ):
                        self._release_flow_identity()
                        return self.async_abort(reason="already_configured")
                    await self.async_set_unique_id(interface_id)
                    self._abort_if_unique_id_configured()
                    return self.async_create_entry(
                        title=f"USB DMX ({path})", data=discovery_data
                    )
                self._release_flow_identity()
                errors["base"] = "cannot_connect"
            except BaseException:
                self._release_flow_identity()
                raise

        self._set_confirm_only()
        return self.async_show_form(
            step_id="usb_confirm",
            description_placeholders={CONF_DEVICE: discovery_data[CONF_DEVICE]},
            errors=errors,
        )

    async def async_step_reconfigure(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Change connection data while preserving config-entry identity."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}
        if user_input is not None:
            raw_path = user_input[CONF_DEVICE]
            if not raw_path.strip():
                errors[CONF_DEVICE] = "invalid_device"
            else:
                normalized_path = _normalize_path(raw_path)
                path = _normalize_path(
                    await _async_get_stable_path(self.hass, normalized_path)
                )
                connection_data = {
                    CONF_BACKEND: user_input[CONF_BACKEND],
                    CONF_DEVICE: path,
                    CONF_INTERFACE_ID: entry.data[CONF_INTERFACE_ID],
                }
                if await self._async_is_device_configured(
                    path, exclude_entry_id=entry.entry_id
                ) or not await self._async_reserve_flow_identity(
                    path=path, interface_id=None
                ):
                    errors["base"] = "already_configured"
                else:
                    try:
                        if await self._async_is_device_configured(
                            path, exclude_entry_id=entry.entry_id
                        ):
                            self._release_flow_identity()
                            errors["base"] = "already_configured"
                        else:
                            valid = await self._async_validate_connection(
                                connection_data
                            )
                            if valid:
                                if await self._async_is_device_configured(
                                    path, exclude_entry_id=entry.entry_id
                                ):
                                    self._release_flow_identity()
                                    errors["base"] = "already_configured"
                                else:
                                    return self.async_update_reload_and_abort(
                                        entry,
                                        data_updates={
                                            CONF_BACKEND: user_input[CONF_BACKEND],
                                            CONF_DEVICE: path,
                                        },
                                    )
                            else:
                                self._release_flow_identity()
                                errors["base"] = "cannot_connect"
                    except BaseException:
                        self._release_flow_identity()
                        raise

        return self.async_show_form(
            step_id="reconfigure",
            data_schema=self._manual_schema(
                backend=entry.data[CONF_BACKEND],
                device=entry.data[CONF_DEVICE],
            ),
            errors=errors,
        )
