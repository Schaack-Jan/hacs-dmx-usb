# Final review comment correction

Corrected the comment above `_validate_forwarded_platforms` to state that HA's
forwarding API hides platform-function failures and `_setup_complete` marks
successful platform setup without certifying each individual entity addition.
No behavior or tests were changed.

Checks:

- `.venv/bin/ruff check custom_components/usb_dmx/__init__.py` — passed: `All checks passed!`
- `git diff --check` — passed (no output).

Commit: `docs: clarify HA platform completion check`.
