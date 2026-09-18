# Milestone 2 Review: Profiles, Routing, And CLI

## Decision

**Pass.** All High (H1–H4) and Medium (M1–M4) findings identified during the
Milestone 2 review have been remediated, verified with targeted regressions,
and validated across Python 3.14.7 and Python 3.11.16 with zero linter errors
and a successful package build. Milestone 3 is unblocked.

## Resolved Findings

| Finding | Severity | Status | Evidence / Verification |
| --- | --- | --- | --- |
| **H1: Settings sync auth overwrite** | High | RESOLVED | Evaluates denied patterns across all path components; rejects source and destination intermediate/target symlinks; fails closed on containment check; atomic replacement under per-profile `.sync` lock. Verified in `tests/test_settings_sync.py`. |
| **H2: Active managed operation removal & resurrection** | High | RESOLVED | Cross-process active run lock (`<state_dir>/runs/<name>/<run_id>.lock`); `remove_profile` rejects active operations (`RuntimeError`); directory removal staged via `.deleting_<name>_<uuid>`; `update_profile_health` does not create missing profiles; backdoor auto-registration removed. Verified in `tests/test_registry.py`. |
| **H3: Health/log concurrency & bounded/latest semantics** | High | RESOLVED | Unique private per-run logs in `<state_dir>/logs/<name>/<run_id>.log` for managed and external profiles; bounded binary seek from EOF for log tails; rolling tail capture of stdout/stderr in streaming mode; latest-signal precedence scanning across stdout, stderr, and logs. Verified in `tests/test_health.py`. |
| **H4: Unknown failure text persisted without redaction** | High | RESOLVED | `sanitize_reason()` strips Bearer tokens, API keys, secrets, email addresses, and truncates long paths to 120 chars before storing in `profiles.json`. Verified in `tests/test_health.py`. |
| **M1: Registry health & metadata accuracy** | Medium | RESOLVED | Untested default health; duplicate add rejected (`ValueError`); pre-disable health and cooldown preserved across disable/enable; `last_selected_at` atomically persisted; untested status tracked and displayed. Verified in `tests/test_registry.py` and `tests/test_routing.py`. |
| **M2: Settings sync before managed launch** | Medium | RESOLVED | Safe synchronization is now default for all managed launches (`auth`, `run`, passthrough); serialized under per-profile `.sync` lock without holding routing lock. Verified in `tests/test_settings_sync.py` and `tests/test_profiles.py`. |
| **M3: Cross-process routing & CLI coverage** | Medium | RESOLVED | Spawned-process round-robin tests with `ProcessPoolExecutor`; `--version`/`-V` handled immediately; malformed `--profile` and misplaced subcommands rejected; `--profile=NAME` equals-syntax supported. Verified in `tests/test_routing.py` and `tests/test_cli_m2.py`. |
| **M4: Retry timing & configurable cooldowns** | Medium | RESOLVED | Multi-unit retry parsing (`s`, `sec`, `m`, `min`, `h`, `hr`); configurable cooldown durations in `Config` with bounds validation. Verified in `tests/test_health.py`. |

## Detailed Remediation Evidence

### H1: Settings synchronization can overwrite or copy authentication data
- **Fix:** In `src/magy/profiles.py:sync_profile_settings()`, all relative path components are checked against denial patterns (`*token*`, `*oauth*`, `*credential*`, `*secret*`, `*account*`, `*history*`, `*conversation*`, `*cache*`, `*log*`, `*trajectory*`, `*install*`, `*.db`, `*.sqlite*`, `*.sock`), preventing nested sensitive directories such as `commands/oauth/payload.bin` from copying.
- **Symlink Protection:** Source symlinks are rejected via `entry.is_symlink()` / `os.path.islink()`. Destination files and intermediate directories are verified to ensure they are not symlinks. Destination containment is validated before directory creation or file writing, failing closed on all `OSError` exceptions.
- **Atomic Writes:** Synchronized files are written to temporary files with `0o600` permissions and atomically moved into place using `os.replace` under a per-profile `.sync` file lock.
- **Regressions:**
  - `tests/test_settings_sync.py::test_sync_rejects_destination_symlink_to_token`
  - `tests/test_settings_sync.py::test_sync_prunes_denied_nested_directory_components`
  - `tests/test_settings_sync.py::test_sync_skips_in_root_source_symlinks`
  - `tests/test_settings_sync.py::test_concurrent_sync_under_lock`

### H2: Active managed operations can be removed and later resurrect profiles
- **Fix:** Implemented cross-process run locking via `track_active_operation(name)` and `get_active_operation_count(name)` using non-blocking `fcntl.flock` on `<state_dir>/runs/<name>/<run_id>.lock`.
- **Removal Safety:** `remove_profile()` checks active operations and raises `RuntimeError` if any run is active. `--force` bypasses confirmation prompts but never bypasses active-operation safety. Staged filesystem cleanup renames the profile directory to `.deleting_<name>_<uuid>` before deletion.
- **Anti-Resurrection:** `update_profile_health()` returns `None` if the profile does not exist in `profiles.json`, preventing child completion from resurrecting deleted profiles. Removed `_ensure_profile_registered()` backdoor from `ensure_profile_layout()`.
- **Regressions:**
  - `tests/test_registry.py::test_remove_profile_active_operation_fails`
  - `tests/test_registry.py::test_update_health_does_not_resurrect_removed_profile`

### H3: Health and log processing is unsafe under concurrency and violates bounded/latest semantics
- **Fix:** Unique private per-run logs are created at `<state_dir>/logs/<name>/<run_id>.log` for all profile runs (both managed and external). Caller-supplied `--log-file` paths are honored and inspected when provided.
- **Bounded Reading:** `read_bounded_log_tail()` uses binary seek from EOF up to 32 KiB and decodes with `errors="replace"` to handle arbitrary or invalid UTF-8 without high memory overhead or decode exceptions.
- **Rolling Tail Capture:** Streaming runs capture rolling tails (last 64 KiB) of stdout and stderr via background reader threads and bounded deques while forwarding output in real-time, preserving stdout scriptability.
- **Latest Signal Precedence:** `classify_run_health()` scans across stdout, stderr, and log content and identifies matches by latest string position (`find_latest_match`), ensuring later rate-limit or quota signals take precedence over older auth notices.
- **Regressions:**
  - `tests/test_health.py::test_classify_run_health_stdout_auth`
  - `tests/test_health.py::test_classify_run_health_latest_signal_precedence`
  - `tests/test_health.py::test_read_bounded_log_tail`

### H4: Unknown failure text is persisted without redaction
- **Fix:** Implemented `sanitize_reason()` in `src/magy/agy.py`. Replaces Bearer tokens and Authorization headers with `[REDACTED_AUTH]`, tokens and keys with `[REDACTED_SECRET]`, email addresses with `[REDACTED_EMAIL]`, and truncates paths/reasons to a maximum length of 120 characters.
- **Integration:** `update_profile_health()` applies `sanitize_reason()` before storing `cooldown_reason` in `profiles.json`.
- **Regressions:**
  - `tests/test_health.py::test_sanitize_reason_redaction`

### M1: Registry health and selection metadata can be reset or remain inaccurate
- **Fix:** `ProfileMetadata.health` defaults to `"untested"`. Untested profiles are eligible for selection (`is_available` allows `"untested"` and `"healthy"`) without falsely claiming proven quota in `magy status`.
- **Duplicate Protection:** `add_profile()` raises `ValueError` if the profile name already exists in the registry.
- **Cooldown Preservation:** `enable_profile()` and `disable_profile()` retain pre-disable health and active cooldowns via `pre_disable_health`.
- **Timestamp Persistence:** `record_profile_selection()` atomically updates `last_selected_at` in `profiles.json` upon profile selection.
- **Regressions:**
  - `tests/test_registry.py::test_add_profile_duplicate_fails`
  - `tests/test_registry.py::test_disable_enable_preserves_cooldown`
  - `tests/test_routing.py::test_selection_persists_last_selected_at`
  - `tests/test_routing.py::test_routing_status`

### M2: Settings synchronization is not applied before every managed launch
- **Fix:** `run_in_profile()` sets `sync_settings=True` by default, ensuring `magy profile auth`, `magy profile run`, and top-level passthrough synchronize allowlisted settings before every launch.
- **Concurrency:** Synchronization is serialized under a dedicated per-profile `.sync` file lock, decoupled from routing cursor locks and child process lifetimes.
- **Regressions:**
  - `tests/test_settings_sync.py::test_concurrent_sync_under_lock`
  - `tests/test_profiles.py::test_cli_profile_create_auth_run`

### M3: Cross-process routing and passthrough behavior lack required coverage
- **Fix:** Added `test_spawned_process_round_robin_distribution` in `tests/test_routing.py` using `ProcessPoolExecutor` to invoke the CLI concurrently across separate processes, verifying exact distribution and persistent cursor advancement.
- **CLI Parsing Hardening:** `src/magy/cli.py` handles `--version` and `-V` immediately with `magy <version>`; rejects missing `--profile` arguments and malformed values (`--profile --`, `--profile=""`); supports `--profile=NAME`; and rejects misplaced subcommands without `--`.
- **Regressions:**
  - `tests/test_routing.py::test_spawned_process_round_robin_distribution`
  - `tests/test_cli_m2.py::test_cli_version_flag`
  - `tests/test_cli_m2.py::test_cli_profile_missing_value_fails`
  - `tests/test_cli_m2.py::test_cli_profile_equals_syntax`
  - `tests/test_cli_m2.py::test_cli_rejects_misplaced_subcommand`

### M4: Provider retry timing and configurable cooldowns are incomplete
- **Fix:** Added configurable cooldown settings in `Config` (`src/magy/config.py`) with numeric bounds validation. Extended `parse_retry_seconds()` in `src/magy/agy.py` to parse seconds, minutes, and hours (`s`, `sec`, `second(s)`, `m`, `min`, `minute(s)`, `h`, `hr`, `hour(s)`).
- **Regressions:**
  - `tests/test_health.py::test_parse_retry_seconds_multi_unit`

## Commands Run

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Linux/Python 3.14.7, 0 errors. |
| `uv run pytest` | PASS | 168 passed in 15.58s on Linux/Python 3.14.7. |
| `uv run --python 3.11 ruff check .` | PASS | Linux/Python 3.11.16, 0 errors. |
| `uv run --python 3.11 pytest` | PASS | 168 passed in 15.55s on Linux/Python 3.11.16. |
| `uv build` | PASS | Built source distribution and binary wheel. |

Zero credentials accessed or stored. Reviewer did not invoke official Agy.
