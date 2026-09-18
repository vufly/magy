# Milestone 2 Implementation: Profiles, Routing, And CLI

## 1. Implementation Summary

Milestone 2 delivers full profile management, safe configuration synchronization,
locked round-robin routing with cooldown management, health signal classification,
and transparent Agy passthrough.

### M2-S1: Profile Registry
- Implemented `ProfileMetadata` in `src/magy/profiles.py` tracking:
  - `name`, `kind` (`managed` | `external`), `home_dir`, `enabled`, `created_at`.
  - `last_selected_at`, `last_success_at`, `last_failure_at`.
  - `health` (`untested` [default], `healthy`, `auth-required`, `rate-limited`, `quota-exhausted`, `disabled`, `timeout`, `unknown-failure`).
  - `cooldown_until` (timestamp) and `cooldown_reason` (bounded redacted string).
  - `pre_disable_health` (preserves prior health and cooldown across disable/enable cycles).
- Persistent storage at `<magy-data>/profiles.json` using atomic locked updates (`src/magy/storage.py`).
- Supported registering external profiles (`magy profile add NAME --current`) recording the path without copying credentials.
- Duplicate profile registration is rejected with `ValueError`.
- Active operation tracking: cross-process run locks held at `<magy-state>/runs/<name>/<run_id>.lock` via non-blocking `fcntl.flock`.
- Profile removal:
  - Actively running profiles reject removal (`RuntimeError`). `--force` bypasses confirmation prompts, never active-run protection.
  - Managed profiles stage deletion via `.deleting_<name>_<uuid>` rename before tree removal.
  - External profiles preserve the user's external home directory while clearing registry state.
- Health updates never resurrect deleted or unregistered profiles; failure reasons are sanitized via `sanitize_reason()`.

### M2-S2: Safe Settings Synchronization
- Implemented `sync_profile_settings(name, real_gemini_dir)` in `src/magy/profiles.py`.
- Synchronizes allowlisted files before each managed profile launch (default on `auth`, `run`, and passthrough):
  - `AGENTS.md`
  - `commands/`
  - `config/`
  - `policies/`
  - `settings.json`
  - `trustedFolders.json`
  - `antigravity-cli/settings.json`
  - `antigravity-cli/keybindings.json`
- Strict exclusion filters (`*token*`, `*oauth*`, `*credential*`, `*secret*`, `*account*`, `*history*`, `*conversation*`, `*cache*`, `*log*`, `*trajectory*`, `*install*`, `*.db`, `*.sqlite*`, `*.sock`) evaluated against every relative path component.
- Symlink containment and protection:
  - Skips any source symlinks.
  - Rejects destination intermediate directories and file targets that are symlinks.
  - Validates destination containment before creating parents; fails closed on resolution errors.
- Atomic updates: copies via private temporary file (`0o600`) and `os.replace` under a per-profile `.sync` file lock.
- External profiles bypass synchronization.

### M2-S3: Round-Robin Selector
- Implemented `select_profile(explicit_name, now)` in `src/magy/routing.py`.
- Persistent cursor stored at `<magy-state>/routing.json` under short file lock (`< 2ms`), serialized independently of process lifetimes.
- Automatically selects available profiles (`healthy` and `untested`), skipping disabled, auth-required, rate-limited, and quota-exhausted profiles until cooldown expiry.
- When cooldown expires, profiles become eligible again.
- Explicit selection uses the requested enabled profile and emits warnings to stderr if in cooldown.
- Atomically updates and persists `last_selected_at` in `profiles.json` upon selection.
- Verified across multiple concurrent OS processes via `ProcessPoolExecutor` with deterministic distribution.
- If no profiles are available, raises `NoAvailableProfileError` detailing status and earliest cooldown remaining.
- `get_routing_status()` returns detailed routing status including `untested_profiles`.

### M2-S4: Health Signal Classification
- Implemented `classify_run_health(exit_code, stdout, stderr, log_content)` in `src/magy/agy.py`.
- Unique private per-run logs created at `<magy-state>/logs/<name>/<run_id>.log` for all profile runs (managed and external).
- Inspects bounded recent output:
  - Bounded binary seek from EOF up to 32 KiB for log files via `read_bounded_log_tail()`, decoding with `errors="replace"` to avoid `UnicodeDecodeError`.
  - Captures rolling tails (last 64 KiB) of both stdout and stderr in streaming mode via background reader threads without buffering child stdout.
  - Inspects caller-supplied `--log-file` paths if present.
- Latest-signal precedence: scans across stdout, stderr, and log content and identifies the chronologically latest signal by position.
- Multi-unit retry parsing: parses retry durations in seconds (`s`, `sec`), minutes (`m`, `min`), and hours (`h`, `hr`).
- Configurable cooldown durations: configurable in `Config` (`cooldown_rate_limit`, `cooldown_quota`, `cooldown_timeout`, `cooldown_unknown`, `cooldown_auth`) with numeric validation.
- Sanitizes failure text via `sanitize_reason()`, redacting Bearer tokens, API keys, passwords, and email addresses, and bounding length to 120 characters.
- Never automatically replays failed tasks; failures only affect future selection.

### M2-S5: CLI Profile Commands
- Extended `src/magy/cli.py` with commands:
  - `magy profile add <name> [--current]`
  - `magy profile create <name>` (alias to add)
  - `magy profile auth <name> [extra_args...]`
  - `magy profile list [--json]`
  - `magy profile show <name> [--json]`
  - `magy profile enable <name>`
  - `magy profile disable <name>`
  - `magy profile reset-health <name>`
  - `magy profile remove <name> [--force]`
  - `magy status [--json]`
  - `magy doctor [--json]`
- Top-level argument handling:
  - `--version` / `-V` prints `magy <version>` and exits cleanly.
  - Rejects missing `--profile` value (`--profile --` or `--profile=""`).
  - Supports `--profile=NAME` equals syntax.
  - Rejects misplaced subcommands when `--` is omitted.

### M2-S6: Transparent Agy Passthrough
- Supports:
  - `magy --profile <name> -- <agy arguments...>`
  - `magy -- <agy arguments...>`
  - `magy <agy arguments...>`
- Terminal streams preserved (stdin interactive, stdout scriptable owned by Agy, stderr forwarded in real-time while captured in bounded buffer for health inspection).
- Informs caller of selected profile via stderr: `[magy] using profile: <name>`.
- Signals (SIGINT, SIGTERM) forwarded cleanly to child process.
- Strictly parses Magy options before `--`, rejecting unknown options.

## 2. Test Verification

| Test Suite | Result | Details |
| --- | --- | --- |
| `tests/test_registry.py` | PASS | Profile metadata, add/remove/enable/disable/reset-health, external profiles, duplicate add rejection, active run removal rejection, anti-resurrection, disable/enable cooldown preservation |
| `tests/test_settings_sync.py` | PASS | Allowlist copying, secret exclusion across all path components, source symlink skip, destination symlink rejection, atomic sync under lock |
| `tests/test_routing.py` | PASS | Round-robin order, cooldown skipping, concurrent cursor advancement, `last_selected_at` persistence, spawned-process distribution, status reporting |
| `tests/test_health.py` | PASS | Success, auth-required, rate-limited with multi-unit retry parsing, quota, timeout, stdout auth, latest-signal precedence, bounded log tail seek, secret redaction |
| `tests/test_cli_m2.py` | PASS | CLI subcommands, JSON outputs, passthrough routing, unknown option rejection, `--version` handling, missing profile arg rejection, equals syntax, misplaced subcommand rejection |
| `tests/test_profiles.py` | PASS | Isolation gates, symlink hardening, deterministic fake concurrency, CLI run/auth sync |
| `tests/test_storage.py` | PASS | File locks, permissions, atomic writes |
| `tests/test_doctor.py` | PASS | Storage and Agy diagnostics |
| `tests/test_snapshot.py` | PASS | Metadata snapshot diffing |
| `tests/test_fake_agy.py` | PASS | Signal handling, error modes |

- `uv run ruff check .`: PASS (0 errors across whole repository).
- `uv run pytest`: 168 passed on Linux (Python 3.14.7).
- `uv run --python 3.11 ruff check .`: PASS.
- `uv run --python 3.11 pytest`: 168 passed on Linux (Python 3.11.16).
- `uv build`: PASS (built wheel and tarball).
