# Task 4 report: documentation, translations and acceptance closure

Base: `288c7f4` (includes reviewed Task 3 fix `0cc8add`), on `feat/dimmer-fade-percentage`.

## Changes and focused evidence

- Added matching EN/DE fixture flow messages for `invalid_fade_time`, `invalid_fade_source` and `unknown_subentry`; required-path tests also cover all native Number/Select names and stable source labels. Existing locale topology/placeholder and actual HA entity metadata tests remain in use.
- Captured translation RED before resource edits: `.venv/bin/pytest -q tests/test_translations.py` produced **3 failed, 49 passed in 0.60s**, precisely the three missing keys. Translation plus new flow cases then produced **54 passed in 0.67s**.
- Added two cases for the evidenced acceptance gap between direct-storage merge tests and actual flow submissions: an open options form and fixture reconfigure form submit before or after queued settings setters. Both preserve UUID, address/name changes, settings and unknown fields. They passed before any production change; no implementation gap was found.
- Existing tests already cover slow writes, USB outage, replacements, metadata, native services/scenes, restore, deletion, reload and platform cleanup. Added only the missing 0.01 s fade endpoint case (shorter than the regular sample interval), verifying one exact completion and the preserved sibling byte: **1 passed in 0.56s**. No production change was necessary.
- README/INSTALLATION now document native roles, 0–1/0–100% conversion/rounding, off-grid service inputs, duration priority and valid zero override, persistence/future-command behavior, live logical states, timing limits, raw behavior, offline/reconnect and central restore, registry ID placeholders and optional manual helper migration.
- One-off documentation validation parsed all four README YAML examples and validated native script/automation actions against the pinned HA script schema. No permanent tests mirror documentation wording or formatting.
- The concise committed acceptance matrix is `docs/superpowers/reports/2026-10-09-dimmer-fade-percentage.md`. It maps every R01–R20 to actual evidence or explicit manual acceptance. Task 2 M1 was already closed by Task 3; no reimplementation or redispatch occurred.

## Final verification

Final local stack: Python **3.14.8**, Home Assistant **2026.9.2**, existing lockfile.

- `uv sync --locked`: success (**155 packages resolved, 139 checked**).
- `uv run pytest -q`: **399 passed in 14.11s**.
- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: **45 files already formatted**.
- `git diff --cached --check`: clean on the final staged changes.

Commit message: `docs: document native dimmer faders and fade controls`.

## Concerns and handoff

Physical hardware acceptance remains unverified. Full host restart, physical timing, unplug/replug and sustained operation remain manual. Task 3's documented dependency on pinned HA `_setup_complete` remains unchanged and covered by real platform-local failure/retry tests. No blocking issue was found during Task 4 acceptance closure.

Task 4 independent review and whole-branch review remain controller work. No remote CI/Hassfest/HACS success is claimed. No pushes, remote refs, PRs, CI reruns, dependency/lock/workflow changes, hardware operations or subagents were performed.
