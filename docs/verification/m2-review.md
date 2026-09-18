# Milestone 2 Review: Profiles, Routing, And CLI

## Decision

**Pending Review.** Milestone 2 implementation and automated verification are complete.

## Exit Criteria Verification

| Criterion | Target | Result | Evidence |
| --- | --- | --- | --- |
| Exact round-robin order | Deterministic | PASS | `tests/test_routing.py::test_round_robin_selection_order` |
| Cooldown skipping | Non-blocking | PASS | `tests/test_routing.py::test_round_robin_skips_cooldown_until_expiry` |
| Parallel selector integrity | Race-free | PASS | `tests/test_routing.py::test_concurrent_cursor_advancement` |
| Allowlist settings sync | Zero leak | PASS | `tests/test_settings_sync.py::test_sync_profile_settings_copies_allowlisted_files` |
| Denied auth exclusion | Zero leak | PASS | `tests/test_settings_sync.py` explicitly tests OAuth/token/db/log files |
| Symlink escape rejection | Fail closed | PASS | `tests/test_settings_sync.py::test_sync_profile_settings_skips_symlink_escaping_real_gemini` |
| Transparent passthrough | Scriptable | PASS | `tests/test_cli_m2.py::test_cli_passthrough_automatic_round_robin` |
| Signal forwarding | Terminate clean| PASS | Signal handlers for SIGINT/SIGTERM in `run_in_profile` |
| Strict argument parsing | Reject unknown | PASS | `tests/test_cli_m2.py::test_cli_passthrough_rejects_unknown_option_before_double_dash` |
| External profile support | Non-destructive| PASS | `tests/test_registry.py::test_remove_profile_external_preserves_filesystem` |

## Review Focus Checks

- **Path traversal and symlink escape:** Settings sync strictly checks containment using `is_relative_to` against the resolved source `.gemini` root and skips escaping symlinks.
- **Lock duration:** File lock on `routing.json` is held strictly for cursor read/advance/write (< 2ms) and is never held during child process execution.
- **Scriptable stdout:** Child process writes directly to `sys.stdout`; Magy diagnostic output is sent strictly to `sys.stderr`.
- **Health parsing:** Inspects bounded recent output (32KB stderr + 32KB log file), mapping errors to specific health states with appropriate cooldowns.
- **No auto-replay:** Failures update profile health for future invocations without automatic replay of failed tasks.

## Commands Run

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Linux/Python 3.14.7, 0 errors. |
| `uv run pytest` | PASS | 149 passed in 14.88s on Linux/Python 3.14.7. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python. |
| `uv run --python 3.11 pytest` | PASS | 149 passed in 15.61s on Linux/Python 3.11.16. |
| `uv build` | PASS | Successfully built wheel and tarball. |
