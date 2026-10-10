# Dimmer Fade Percentage Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development. Execute test-first, with independent task reviews and a whole-branch review. Checkboxes track completion.

**Goal:** Add native normalized dimmer faders and persistent interface/fixture fade controls with live logical states.

**Architecture:** The controller owns frame writes and one monotonic transition task per address. A shared `DimmerRuntime` per fixture owns logical state, command/removal locking and restore. `FadeSettings` owns current-data storage merges; light, numbers and select subscribe to shared state.

**Tech Stack:** Python >=3.14.2, homeassistant==2026.9.2, existing uv.lock, pytest and Ruff.

**Spec:** `docs/superpowers/specs/2026-10-09-dimmer-fade-percentage-design.md`

## Global Constraints

- Branch `feat/dimmer-fade-percentage`, base `f025a456138d2cb0884d850d5ffcf4a22e9af82a`. No main writes, force pushes, merge or release.
- Home Assistant minimum `2026.9.0`; no new runtime dependencies or backend/protocol/universe changes.
- Keep fixture UUIDs, parent device ownership, existing light/raw unique IDs and raw immediate 0–255 writes.
- Fade settings: finite int/float, exclude bool, range 0–2 inclusive, normalized to 2 decimals; source exactly `global` or `individual`. Missing settings mean 0/global.
- Fader 0–1 step 0.01 without unit; target `int(round(value * 255))`, display `round(published_brightness / 255, 2)` when on, otherwise 0. Off-grid inputs are accepted without initial quantization.
- Explicit light transition 0–6553 inclusive overrides configured times, including explicit 0.
- Settings update storage and runtime without reload/DMX, retain unknown fields, apply only to future commands.
- One controller transition timebase for physical bytes and logical brightness; callback updates even if DMX byte unchanged; generation guards forbid obsolete publication.
- Initial zero frame, one immediate restore, shutdown blackout option, offline fade and reconnect current full frame remain.
- No changes to user dashboard YAML or helper entities. Hardware tests must be reported as unverified without hardware.
- User's current instruction to execute authorizes implementation; description/topics are additionally authorized despite the design's exclusion of repository metadata changes.

## Review Focus

1. Restore with disabled light and active fader resolves once: newest usable stored brightness wins, with Light winning equal-recency ties and supplying the legacy state when no newer Fader exists.
2. Fixture deletion racing queued light/fader/settings writes must be terminal, preserve siblings, and never recreate a child.
3. Equal positive DMX min/max and 0/0 must still publish logical fade progress and exact off.
4. Slow writes and USB outage must not stretch software timing cumulatively or cause compensation bursts/stale finals.
5. Reconfigure/options flows racing settings setters must merge fresh data and preserve unknown fields and overrides.

## Requirements Map

| Requirements | Tasks |
|---|---|
| R01, R03–R07, R11 | 1, 3 |
| R02, R12–R15, R19 | 2, 3, 4 |
| R08–R10 | 2, 3 |
| R16–R18 | 2, 3 |
| R20 | all tasks, independent reviews, progress ledger, Conventional Commits |

### Task 1: Persistent fade configuration

**Files:** Modify `const.py`, `models.py`, `config_flow.py` under `custom_components/usb_dmx/`; create `settings.py`; extend `tests/test_models.py`, `tests/test_subentry_flow.py`, `tests/test_options_flow.py`; create `tests/test_fade_settings.py`.

**Interfaces:**
- Produce `FadeSource(StrEnum)` with values `global`, `individual`; `FixtureConfig.fade_time: float = 0.0`, `FixtureConfig.fade_source: FadeSource = FadeSource.GLOBAL`.
- Produce `validate_fade_time(value: object) -> float` and `validate_fade_source(value: object) -> FadeSource`, raising stable `FixtureValidationError` for malformed stored settings.
- Produce `FadeSettings(hass, entry)` with `global_fade_time: float`, `add_listener(callback: Callable[[], None]) -> Callable[[], None]`, `async_set_global_fade_time(value: object) -> None`, `async_set_fixture_fade_time(fixture_id: str, value: object) -> None`, `async_set_fixture_fade_source(fixture_id: str, value: object) -> None`, and `fixture_settings(fixture_id: str) -> tuple[float, FadeSource]` reading current subentry data.
- Storage keys `global_fade_time`, `fade_time`, `fade_source`. Setters recheck existence after their common lock, use HA entry/subentry updates with fresh-data merges, and publish after successful updates. No runtime brightness/write dependency.

- [x] Write failing tests: defaults and roundtrip; values 0/2/0.015; reject True, strings, NaN, Inf, negative and >2; exact source; malformed stored configuration. Include raw identity compatibility.
- [x] Run targeted tests and record expected failures before production edits.
- [x] Implement constants/model/settings and flow merges. Reconfigure builds final data from the latest subentry plus changed six fixture fields, retains settings/unknown fields, rejects a deleted child. Existing options flow retains unknown/global keys. Config entry/subentry updates are synchronous inside the same event-loop critical section; don't hold stale dictionaries across awaits.
- [x] Verify simultaneous setters, no reload/DMX, interface isolation, source changes retaining individual time and individual zero; model/flow/options tests and full suite; Ruff on changed files.
- [x] Commit `feat: persist interface and dimmer fade settings`; report RED/GREEN commands and results.

### Task 2: Monotonic controller and shared dimmer runtime

**Files:** Modify `controller.py`, `__init__.py`; create `dimmer.py`; extend `tests/test_controller.py`, `tests/test_init.py`; create `tests/test_dimmer.py`.

**Interfaces:**
- Consume Task 1 model/settings APIs.
- Extend `DmxController.async_transition_channel(address: int, value: int, duration: float, *, progress_callback: Callable[[float], None] | None = None) -> None`. Callback receives fraction 0–1 from the controller's monotonic timer, including exact completion even for unchanged byte. Existing callers remain compatible. Add `async_cancel_transition(address: int) -> None` for no-write unload.
- Produce `DimmerRuntime(controller, fixture: FixtureConfig, settings: FadeSettings | None = None)` exposing `logical_brightness: float`, `brightness: int | None`, `is_on: bool`, `level: float`, `last_on_brightness: int | None`, `fade_time: float`, `fade_source: FadeSource`, `add_listener(callback) -> unsubscribe`, `async_turn_on(brightness: object | None = None, *, transition: object | None = None)`, `async_turn_off(*, transition: object | None = None)`, `async_set_level(value: object)`, `async_restore(brightness: object | None, last_on_brightness: object | None = None)`, `async_remove(*, blackout: bool)`.
- Extend `UsbDmxRuntime` with defaulted `settings` and `dimmers` fields to preserve positional compatibility for current test factories. Setup initializes settings and every dimmer before platform forwarding; add `Platform.SELECT`. Entity tests with manually constructed legacy runtime will be accommodated by Task 3 centralized lazy initialization.

- [x] RED tests for progress timing, unchanged-byte callback, slow backend duration/endpoints; runtime configured priority, rounding 0/0.3/0.5/1/off-grid/tiny positive, live brightness including clamp-to-1, remembered positive targets.
- [x] Implement monotonic elapsed interpolation with at most ~40 steps/s, exact final endpoint/write, no catchup bursts, cancellation and stale-generation guard. Keep physical byte interpolation independent of logical min/max mapping. Callback must run before slow awaited writes so current logical state matches admitted progress, and cancelled tasks must not emit completion.
- [x] Implement shared FIFO command/removal lock; duration sampled once at admission; settings changes no current fade impact. Terminal removal rejects queued commands; unload can cancel with no zero write. Restore once, no fade, settings-only entities never initiate it.
- [x] Test replacing fades under alternating commands, equal ranges, offline software progress/replay, deletion queues/idempotence, startup safety and full suite; Ruff on changed files.
- [x] Commit `feat: share live dimmer state with monotonic fades`; report any existing integration tests intentionally awaiting Task 3.

### Task 3: HA light/number/select integration and restore

**Files:** Modify `entity.py`, `light.py`, `number.py`; create `select.py` and focused `restore.py` if useful; extend HA platform/service/scene/lifecycle tests, create `tests/test_dimmer_entities.py`, `tests/test_select.py`.

**Interfaces:**
- Consume Tasks 1/2. All dimmer companion entities reference the same runtime by fixture UUID. Ensure manually constructed `UsbDmxRuntime` tests use the same helper initialization as production.
- Light unique ID stays `<entry_id>_<uuid>`; level suffix `_level`; fade time `_fade_time`; source `_fade_source`; parent global `<entry_id>_global_fade_time`.
- Level regular Number: 0–1, .01, SLIDER, no unit. Fade Numbers: 0–2, .01, BOX, seconds, DURATION, configuration category. Source Select options `global`,`individual`, configuration category. Global is parent-owned even with zero fixtures; companions subentry-owned; all share parent interface device.
- Entity callbacks deduplicate changed visible states; controls only subscribe to relevant settings, output controls mirror controller availability. Remove all subscriptions during entity removal.
- Real HA restore state is resolved once centrally at the first active light/fader, preferring stored light state via registry even if light disabled. Light/number restore extra data includes last positive requested target. Off has no visible brightness attribute; restored legacy off brightness may supply remembered target. No restoration for settings-only entities.

- [x] RED tests for entity creation/ownership/IDs, no-dimmer global, Light/Fader concurrent commands and shared live intermediate states, setting commands and scenes, config UI metadata, strict input boundaries.
- [x] Replace light optimistic state with runtime properties; forward commands to runtime. Keep raw entities behavior/identity. Add native companions and role translation keys, preserve fixture names.
- [x] RED→GREEN restore tests with real HA extra data, platform order, disabled light and disabled number, malformed restore, zero startup, one restore write. Replace existing optimistic transition assertions with live-state checks, retain other regressions.
- [x] Verify queued deletion, siblings/parent device, type/address/range reconfigure cleanup of obsolete registry entities and old tasks, normal unload without blackout, settings changes don't reload; real HA native light/number/select services and scenes. Run full suite and Ruff.
- [x] Commit `feat: expose normalized dimmer and fade controls`; report exact failures if any.

### Task 4: Translations, documentation and regression closure

**Files:** Modify `translations/en.json`, `translations/de.json`, `README.md`, `INSTALLATION.md`; extend translation/repository tests and any missing acceptance tests from the requirements map.

- [x] Verify every R01–R20 has code/test or explicit manual acceptance coverage. Add missing behavior tests first and fix only evidenced gaps. Check interleaved options/reconfigure/settings submissions and slow/outage/replacement lifecycle boundaries.
- [x] Add EN/DE matching translation topology, new number/select names and source labels; validate with translation tests and HA entity metadata tests.
- [x] Document 0–1 normalized vs 0–100% display, rounding, priority explicit > individual > global, valid zero override, live logical output rather than measured feedback, time-resolution limits, optional manual helper migration, actual registry entity IDs, no hardware guarantees. Supply native fader YAML/service examples using placeholder registry IDs.
- [x] Run `uv sync --locked`, `uv run pytest -q`, `uv run ruff check .`, `uv run ruff format --check .`; report actual counts. Do not change dependencies/lock to fix unrelated package updates.
- [x] Commit `docs: document native dimmer faders and fade controls`.

## Completion

- [x] Independent whole-branch review; fix Important/Critical findings with RED→GREEN evidence and scoped independent re-review.
- [ ] Push feature branch via GitHub connector; inspect actual Tests/Hassfest/HACS workflow results.
- [ ] Set requested description and add `home-assistant`, `hacs`, `custom-integration`, `dmx`, `usb-dmx`, preserving any existing unrelated topics. The connector has no repository administration mutation; use another authorized channel only if available, otherwise report concrete blocker.
- [ ] Report branch/commit, verified checks, metadata result and hardware tests not performed. Do not merge or release.

## Reviewed execution notes

Task 3 completed at `0cc8add`: 385 full-suite tests passed before the focused setup fix; actual Number/Select platform failure RED 2 failed, lifecycle/setup GREEN 24 passed after the fix. Independent task review and scoped fix review approved the native entity/restore/lifecycle work. The prior setup subscription cleanup gap is closed. Task 4 owns the final full-suite run.

Restore-source ruling: a disabled Light may retain stale cache while its active Fader records a newer brightness. Prefer the newest usable record, with Light priority at equal recency; rework is confined to this feature branch if this interpretation is revised.

Task 4 completed at local commit `8739eeb`: locked sync, 399 passing tests, Ruff lint/format and diff checks passed. Independent specification and quality review approved the documentation, translations and acceptance matrix without findings. Physical hardware remains unverified.

Commit IDs in these execution notes refer to local development history. Publishing through the GitHub connector may produce different commit IDs due to author/timestamp metadata; every published tree must match its verified local tree SHA before the single new branch is created. No intermediate remote branch updates, pull request or CI reruns are planned.

Final whole-branch specification review passed. Its sole minor comment correction was addressed at `3893f0b` and the scoped re-review approved it with no findings. The root's fresh locked-stack run passed 399 tests, Ruff lint/format (45 files), diff checks and unchanged lock/workflow/original-upload checks. An AST comparison confirmed that the final comment correction did not alter executable behavior.

Execution rulings: the explicit execution request superseded the design's historical extra approval gates and additionally authorized repository metadata; any mistaken interpretation costs reviewable feature-branch rework, not a main change. Select forwarding moved from Task 2 to Task 3 so the intermediate setup never imported an absent platform module; any reversal costs task-ordering rework, not a final feature difference. The restore-source ruling above avoids stale disabled-Light cache; reversing it costs restore-priority rework without changing fixture identities.

Publication, actual remote workflow results and repository metadata status are reported in the handoff. Those completion checkboxes record the pre-publication state of this verified tree; no documentation-only follow-up push will be used to update them. Physical hardware remains unverified.
