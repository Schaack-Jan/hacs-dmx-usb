# Native dimmer faders and fades: acceptance evidence

Scope: R01–R20 in `docs/superpowers/specs/2026-10-09-dimmer-fade-percentage-design.md`, on `feat/dimmer-fade-percentage`. Evidence below refers to the final local locked-stack suite, not remote CI or physical hardware.

## Requirement coverage

| ID | Actual automated evidence or manual acceptance |
|---|---|
| R01 | `test_global_exists_without_fixtures`, `test_settings_concurrent_merges_and_interface_isolation`: one parent-owned global control, isolated interface settings. |
| R02 | `test_native_companions_ownership_metadata_and_setting_services`: existing Light plus Level/Fade time/Source roles and shared parent device. |
| R03 | Native metadata test above; model/settings numeric-boundary tests: 0–2 s, 0.01 s step, zero defaults and strict finite numeric validation. |
| R04 | Model default/legacy fixture tests and `test_settings_defaults_persist_and_notify_after_storage`: absent fields use 0/global. |
| R05 | `test_settings_priority_future_commands_and_no_output`, `test_native_settings_persist_override_and_source_on_reload`: individual zero overrides positive global time. |
| R06 | `test_settings_concurrent_merges_and_interface_isolation`: global updates and source changes retain individual values. |
| R07 | Settings reconstruction test and native settings reload test: persisted global/individual/source values survive reconstruction and actual reload. Full host restart remains a manual hardware acceptance step. |
| R08 | Light service tests, `test_light_fader_live_replacement_and_exact_off`, `test_scene_uses_native_level_and_configured_fade`: Light on/off, fader and real HA scene reproduction use shared fades. |
| R09 | Runtime priority test and Light transition validation/service tests: explicit duration, including zero, wins; existing 0–6553 range preserved. |
| R10 | `test_alternating_commands_cancel_stale_finals`, native live replacement test, controller concurrent replacement/cancellation tests: current-value starts, latest admitted command and no obsolete finals/tasks. |
| R11 | Runtime priority test, native metadata/settings service test, role callback deduplication test: settings do not write DMX/reload or alter admitted duration. |
| R12 | Native metadata test: Level is a regular Number, slider, 0–1, step 0.01, no percent unit. |
| R13 | `test_level_rounding_and_remembered_targets`, `test_native_fader_conversion_accepts_off_grid_input`: endpoints, 0.30→76, 0.50→128, tiny and off-grid inputs, two-decimal display. |
| R14 | Logical-range and native live state tests plus Light transition services: intermediate values, positive logical clamp-to-1, Light on until exact zero, absent off brightness. |
| R15 | Logical-range tests (0/0, equal positive, limited range) prove state is independent of physical byte mapping. README/INSTALLATION explain calculated output; measured lamp response is unverified. |
| R16 | `test_startup_sends_zero_before_optional_restore`, `test_restore_once_prefers_legacy_light_with_disabled_companions`, serialized HA restore-storage tests, init/shutdown tests: zero first, single immediate restore, platform order/disabled output roles, malformed restore and optional blackout. |
| R17 | `test_offline_progress_and_reconnect_replays_current_frame`, `test_offline_output_availability_and_live_reconnect`: software completion while disconnected, unavailable output roles/editable settings, current full-frame replay. USB unplug/replug remains physically unverified. |
| R18 | Existing Light identity, raw Number ownership/service and subentry reconfigure tests: immutable UUIDs, existing IDs/device ownership, raw immediate 0–255 behavior. Native lifecycle reconfigure tests preserve identities and clean old address/type roles. |
| R19 | README/INSTALLATION provide optional manual helper/dashboard migration and actual registry-ID instructions. Implementation contains no helper/dashboard migration; user dashboard replacement/removal is manual. |
| R20 | Task reports, independent review records and `progress.md` record test-first execution and Conventional Commits on the feature branch. Translation RED: 3 missing-key failures. Task 4 adds acceptance-only flow interleaving and 0.01 s endpoint tests without production fixes. Task 4 and final whole-branch review remain controller steps after this commit. |

Additional boundaries are covered by `test_slow_writes_use_elapsed_time_and_publish_before_write` (elapsed-time progression and bounded writes), `test_short_fade_below_sample_interval_reaches_exact_endpoint` (0.01 s exact completion), and `test_open_flows_and_queued_settings_preserve_all_updates` (real open options/reconfigure forms submitted before or after queued setters, preserving fresh and unknown fields). Deletion/setup failure/unload coverage includes actual platform-local Number/Select failure rollback, sibling interface preservation and successful retry. Task 2's deferred subscription-cleanup finding was closed in Task 3.

EN/DE translation topology and placeholders match; required entity role names/source labels and invalid fade/deleted-fixture flow messages are tested. A one-off check parsed all four README YAML blocks and validated native script/automation actions with the installed HA script schema.

## Final local verification

Python **3.14.8**, Home Assistant **2026.9.2**, existing `uv.lock`:

- `uv sync --locked`: success; resolved 155 packages, checked 139 packages.
- `uv run pytest -q`: **399 passed in 14.11s**.
- `uv run ruff check .`: **All checks passed!**
- `uv run ruff format --check .`: **45 files already formatted**.
- `git diff --cached --check`: clean on the final staged changes.

No dependency/lockfile, workflow trigger, protocol/backend/universe or original-upload changes. Remote CI, Hassfest and HACS results are not claimed here; the controller assesses actual workflow results after the single final publication.

## Physical acceptance still unverified

Without attached hardware, concurrent real faders, Light on/off response, global/individual/zero timing, USB unplug/replug, physical restart/reload, 0.01 s/2 s output timing and sustained operation remain unverified. Software tests do not establish adapter compatibility, measured lamp brightness or a real-time guarantee.
