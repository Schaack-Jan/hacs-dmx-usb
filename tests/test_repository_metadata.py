"""Validate release metadata and continuous-integration contracts."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import yaml
from PIL import Image

_ROOT = Path(__file__).parents[1]
_CHECKOUT_REF = "actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1"
_SETUP_UV_REF = "astral-sh/setup-uv@bec219d24cd3e171d82865faccec33120bb574f4"


def _load_json(relative_path: str) -> dict[str, Any]:
    """Load one repository JSON object."""
    with (_ROOT / relative_path).open(encoding="utf-8") as file:
        return json.load(file)


def _load_workflow(name: str) -> dict[str, Any]:
    """Parse one workflow with a YAML 1.1-compatible loader."""
    with (_ROOT / ".github" / "workflows" / name).open(encoding="utf-8") as file:
        return yaml.safe_load(file)


def _uses(workflow: dict[str, Any]) -> list[str]:
    """Collect action references from every job step."""
    return [
        step["uses"]
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if "uses" in step
    ]


def test_hacs_and_manifest_metadata_match_release_scope() -> None:
    """Metadata must retain the supported HA version and narrow protocol scope."""
    hacs = _load_json("hacs.json")
    manifest = _load_json("custom_components/usb_dmx/manifest.json")

    assert hacs == {
        "name": "USB DMX",
        "homeassistant": "2026.9.0",
    }
    assert manifest["domain"] == "usb_dmx"
    assert manifest["name"] == "USB DMX"
    assert manifest["codeowners"] == ["@Schaack-Jan"]
    assert manifest["dependencies"] == ["usb"]
    assert manifest["requirements"] == []
    assert manifest["version"] == "0.1.0"
    assert manifest["documentation"] == "https://github.com/Schaack-Jan/hacs-dmx-usb"
    assert (
        manifest["issue_tracker"]
        == "https://github.com/Schaack-Jan/hacs-dmx-usb/issues"
    )
    assert "usb" not in manifest


def test_brand_icon_is_a_square_repository_png() -> None:
    """HACS must receive the required local PNG asset without format ambiguity."""
    with Image.open(_ROOT / "brand" / "icon.png") as icon:
        assert icon.format == "PNG"
        assert icon.size == (256, 256)


def test_test_workflow_is_frozen_and_read_only() -> None:
    """The test workflow must exercise the lockfile with the supported Python."""
    workflow = _load_workflow("tests.yml")
    events = workflow["on"]
    commands = "\n".join(
        step.get("run", "")
        for job in workflow["jobs"].values()
        for step in job["steps"]
    )

    assert set(events) == {"push", "pull_request", "workflow_dispatch"}
    assert workflow["permissions"] == {"contents": "read"}
    assert _uses(workflow) == [_CHECKOUT_REF, _SETUP_UV_REF]
    setup_uv_step = next(
        step
        for job in workflow["jobs"].values()
        for step in job["steps"]
        if step.get("uses") == _SETUP_UV_REF
    )
    assert setup_uv_step["with"]["python-version"] == "3.14.2"
    assert "uv sync --frozen" in commands
    assert "uv run pytest -q" in commands
    assert "uv run ruff check ." in commands
    assert "uv run ruff format --check ." in commands


def test_validator_workflows_are_separate_and_run_daily() -> None:
    """Hassfest and HACS must track their official validators without skips."""
    hassfest = _load_workflow("hassfest.yml")
    hacs = _load_workflow("hacs.yml")

    for workflow in (hassfest, hacs):
        assert set(workflow["on"]) == {
            "push",
            "pull_request",
            "schedule",
            "workflow_dispatch",
        }
        assert workflow["permissions"] in ({}, {"contents": "read"})
        assert workflow["on"]["schedule"] == [{"cron": "0 0 * * *"}]

    assert _uses(hassfest) == [
        _CHECKOUT_REF,
        "home-assistant/actions/hassfest@master",
    ]
    assert _uses(hacs) == ["hacs/action@main"]
    hacs_step = next(
        step
        for job in hacs["jobs"].values()
        for step in job["steps"]
        if step.get("uses") == "hacs/action@main"
    )
    assert hacs_step["with"] == {"category": "integration"}
