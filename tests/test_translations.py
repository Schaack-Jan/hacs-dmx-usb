"""Validate USB DMX runtime translation resources."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest

from custom_components.usb_dmx.config_flow import (
    FixtureSubentryFlow,
    UsbDmxConfigFlow,
)

_INTEGRATION_DIR = Path(__file__).parents[1] / "custom_components" / "usb_dmx"
_TRANSLATION_FILES = (
    _INTEGRATION_DIR / "strings.json",
    _INTEGRATION_DIR / "translations" / "en.json",
    _INTEGRATION_DIR / "translations" / "de.json",
)
_PLACEHOLDER_PATTERN = re.compile(r"(?<!{){([a-zA-Z0-9_]+)}(?!})")


def _load_json(path: Path) -> dict[str, Any]:
    """Load one translation resource through the standard JSON parser."""
    with path.open(encoding="utf-8") as file:
        return json.load(file)


def _key_tree(value: object) -> object:
    """Return only mapping keys and sequence positions for parity checks."""
    if isinstance(value, dict):
        return {key: _key_tree(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_key_tree(nested) for nested in value]
    return None


def _placeholder_tree(value: object) -> object:
    """Return placeholders for every translated leaf."""
    if isinstance(value, dict):
        return {key: _placeholder_tree(nested) for key, nested in value.items()}
    if isinstance(value, list):
        return [_placeholder_tree(nested) for nested in value]
    if isinstance(value, str):
        return sorted(_PLACEHOLDER_PATTERN.findall(value))
    return []


def _get_path(data: dict[str, Any], path: str) -> object:
    """Resolve a dotted translation path."""
    current: object = data
    for part in path.split("."):
        assert isinstance(current, dict)
        current = current[part]
    return current


@pytest.mark.parametrize(
    "required_path",
    [
        "title",
        "config.abort.already_configured",
        "config.abort.already_in_progress",
        "config.abort.invalid_discovery_info",
        "config.abort.reconfigure_successful",
        "config.error.cannot_connect",
        "config.error.invalid_device",
        "config.step.user.data.backend",
        "config.step.user.data.device",
        "config.step.manual.data.backend",
        "config.step.manual.data.device",
        "config.step.usb_confirm.description",
        "config.step.reconfigure.data.backend",
        "config.step.reconfigure.data.device",
        "config_subentries.fixture.entry_type",
        "config_subentries.fixture.initiate_flow.user",
        "config_subentries.fixture.initiate_flow.reconfigure",
        "config_subentries.fixture.abort.reconfigure_successful",
        "config_subentries.fixture.error.invalid_address",
        "config_subentries.fixture.error.invalid_range",
        "config_subentries.fixture.error.duplicate_address",
        "config_subentries.fixture.error.duplicate_name",
        "config_subentries.fixture.error.invalid_fixture",
        "config_subentries.fixture.step.user.data.type",
        "config_subentries.fixture.step.user.data.name",
        "config_subentries.fixture.step.user.data.address",
        "config_subentries.fixture.step.user.data.minimum",
        "config_subentries.fixture.step.user.data.maximum",
        "config_subentries.fixture.step.reconfigure.data.type",
        "options.error.invalid_options",
        "options.step.init.data.startup_behavior",
        "options.step.init.data.blackout_on_shutdown",
        "selector.backend.options.serial_pro",
        "selector.fixture_type.options.dimmer",
        "selector.fixture_type.options.raw",
        "selector.startup_behavior.options.restore",
        "selector.startup_behavior.options.zero",
        "device.interface.name",
        "entity.light.dimmer.name",
        "entity.number.raw_channel.name",
    ],
)
def test_canonical_strings_cover_runtime_translation_paths(
    required_path: str,
) -> None:
    """Removing a flow- or entity-facing key must fail resource validation."""
    assert isinstance(_get_path(_load_json(_TRANSLATION_FILES[0]), required_path), str)


def test_translation_files_match_canonical_tree_and_placeholders() -> None:
    """Locales must not lose keys or the placeholders supplied by runtime code."""
    canonical, english, german = map(_load_json, _TRANSLATION_FILES)

    assert english == canonical
    assert _key_tree(german) == _key_tree(canonical)
    assert _placeholder_tree(german) == _placeholder_tree(canonical)


def test_static_selectors_reference_translated_options() -> None:
    """Static option values must point at the selector translation namespaces."""
    manual_schema = UsbDmxConfigFlow._manual_schema()
    fixture_schema = FixtureSubentryFlow()._schema(None)

    assert manual_schema.schema["backend"].config["translation_key"] == "backend"
    assert fixture_schema.schema["type"].config["translation_key"] == "fixture_type"
