# Milestone 2 Implementation: Profiles, Routing, And CLI

## 1. Implementation Summary

Milestone 2 delivers full profile management, safe configuration synchronization,
locked round-robin routing with cooldown management, health signal classification,
and transparent Agy passthrough.

### M2-S1: Profile Registry
- Implemented `ProfileMetadata` in `src/magy/profiles.py` tracking:
  - `name`, `kind` (`managed` | `external`), `home_dir`, `enabled`, `created_at`.
  - `last_selected_at`, `last_success_at`, `last_failure_at`.
  - `health` (`healthy`, `auth-required`, `rate-limited`, `quota-exhausted`, `disabled`, `timeout`, `unknown-failure`).
  - `cooldown_until` (timestamp) and `cooldown_reason` (bounded redacted string).
- Persistent storage at `<magy-data>/profiles.json` using atomic locked updates (`src/magy/storage.py`).
- Supported registering external profiles (e.g. `magy profile add NAME --current`) which records the path without copying credentials.
- Profile removal rejects active operations and requires `--force` in non-interactive sessions; managed profiles delete filesystem layouts while external profiles preserve the user's external directory.

### M2-S2: Safe Settings Synchronization
- Implemented `sync_profile_settings(name, real_gemini_dir)` in `src/magy/profiles.py`.
- Synchronizes allowlisted files before each managed profile launch:
  - `AGENTS.md`
  - `commands/`
  - `config/`
  - `policies/`
  - `settings.json`
  - `trustedFolders.json`
  - `antigravity-cli/settings.json`
  - `antigravity-cli/keybindings.json`
- Strict exclusion filters (`*token*`, `*oauth*`, `*credential*`, `*secret*`, `*account*`, `*history*`, `*conversation*`, `*cache*`, `*log*`, `*trajectory*`, `*install*`, `*.db`, `*.sqlite*`, `*.sock`).
- Symlink containment: skips any source symlinks that resolve outside `.gemini`.
- Permission enforcement: directories created with `0o700` and synced files with `0o600`.
- External profiles bypass synchronization.

### M2-S3: Round-Robin Selector
- Implemented `select_profile(explicit_name, now)` in `src/magy/routing.py`.
- Persistent cursor stored at `<magy-state>/routing.json` under short file lock (`< 2ms`), serialized independently of process lifetimes.
- Automatic selection skips disabled, auth-required, rate-limited, and quota-exhausted profiles until cooldown expiry.
- When cooldown expires, profiles become eligible again.
- Explicit selection uses the requested enabled profile and emits warnings to stderr if in cooldown.
- Deterministic cursor advancement tested across concurrent threads without lost updates.
- If no profiles are available, raises `NoAvailableProfileError` detailing status and earliest cooldown remaining.

### M2-S4: Health Signal Classification
- Implemented `classify_run_health(exit_code, stdout, stderr, log_content)` in `src/magy/agy.py`.
- Inspects bounded recent stderr (32KB) and log file (32KB).
- Signal mapping:
  - `exit_code == 0`: `healthy` (clears cooldown, records `last_success_at`).
  - Auth required ("Please sign in", "auth login", "credentials expired"): `auth-required` (cooldown 86400s).
  - Rate limit ("429", "rate limit reached"): `rate-limited`, parses retry duration if present (or default 60s).
  - Quota exhausted ("exceeded your current quota"): `quota-exhausted` (cooldown 3600s).
  - Timeout ("request timed out", "deadline exceeded"): `timeout` (cooldown 30s).
  - Other nonzero: `unknown-failure` (cooldown 15s).
- Injects `--log-file` when caller has not specified one.
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
| `tests/test_registry.py` | PASS | Profile metadata, add/remove/enable/disable/reset-health, external profiles |
| `tests/test_settings_sync.py` | PASS | Allowlist copying, secret exclusion, symlink escape rejection, external bypass |
| `tests/test_routing.py` | PASS | Round-robin order, cooldown skipping, concurrent cursor advancement, status |
| `tests/test_health.py` | PASS | Success, auth-required, rate-limited with retry regex, quota, timeout |
| `tests/test_cli_m2.py` | PASS | CLI subcommands, JSON outputs, passthrough routing, unknown option rejection |
| `tests/test_profiles.py` | PASS | Isolation gates, symlink hardening, deterministic fake concurrency |
| `tests/test_storage.py` | PASS | File locks, permissions, atomic writes |
| `tests/test_doctor.py` | PASS | Storage and Agy diagnostics |
| `tests/test_snapshot.py` | PASS | Metadata snapshot diffing |
| `tests/test_fake_agy.py` | PASS | Signal handling, error modes |

- `uv run ruff check .`: PASS (0 errors across whole repository).
- `uv run pytest`: 149 passed on Linux (Python 3.14.7).
- `uv run --python 3.11 ruff check .`: PASS.
- `uv run --python 3.11 pytest`: 149 passed on Linux (Python 3.11.16).
- `uv build`: PASS (built wheel and tarball).
