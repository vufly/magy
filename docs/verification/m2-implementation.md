# Milestone 2 Implementation: Profiles, Routing, And CLI

## 1. Implementation Summary

Milestone 2 delivers full profile management, safe configuration synchronization,
locked round-robin routing with cooldown management, health signal classification,
and transparent Agy passthrough.

### M2-S1: Profile Registry & Lifecycle Leases
- Implemented `ProfileMetadata` in `src/magy/profiles.py` tracking:
  - `name`, `kind` (`managed` | `external`), `home_dir`, `enabled`, `created_at`.
  - `incarnation_id` (immutable UUID hex generated per creation to prevent ABA replacement races).
  - `last_selected_at`, `last_success_at`, `last_failure_at`.
  - `health` (`untested` [default], `healthy`, `auth-required`, `rate-limited`, `quota-exhausted`, `disabled`, `timeout`, `unknown-failure`).
  - `cooldown_until` (timestamp) and `cooldown_reason` (bounded redacted string).
  - `pre_disable_health` (preserves prior health and cooldown across disable/enable cycles).
- Persistent storage at `<magy-data>/profiles.json` using atomic locked updates (`src/magy/storage.py`).
- Supported registering external profiles (`magy profile add NAME --current`) recording the path without copying credentials.
- Duplicate profile registration is rejected with `ValueError`.
- Atomic Lifecycle Leases (replacing racy count-then-act):
  - Shared lifecycle lease (`fcntl.LOCK_SH`) acquired at `<magy-state>/leases/<name>.lease` before layout validation, settings sync, or launch.
  - Exclusive lifecycle lease (`fcntl.LOCK_EX | fcntl.LOCK_NB`) acquired during profile mutation or removal.
  - `get_active_operation_count()` probes the lifecycle lease file non-blockingly.
- Profile removal:
  - Actively running profiles reject removal immediately via exclusive lease contention.
  - Incarnation verification: validates `incarnation_id` under registry lock to prevent deleting replaced profiles.
  - Transactional cleanup: managed profiles stage directory rename via `.deleting_<name>_<uuid>` before registry mutation. If registry update fails, the rename is rolled back. Deletion failures are reported rather than silently ignored.
  - External profiles preserve the user's external home directory while clearing registry state.
- Health updates never resurrect deleted or unregistered profiles; failure reasons are sanitized via `sanitize_reason()`.

### M2-S2: Safe Settings Synchronization
- Implemented `sync_profile_settings(name, real_gemini_dir)` in `src/magy/profiles.py`.
- Synchronizes allowlisted files before each managed profile launch (default on `auth`, `run`, and passthrough):
  - `AGENTS.md`, `GEMINI.md`
  - `commands/`, `config/`, `policies/`
  - `settings.json`, `trustedFolders.json`
  - `antigravity-cli/settings.json`, `antigravity-cli/keybindings.json`
- Strict exclusion filters (`*token*`, `*oauth*`, `*credential*`, `*secret*`, `*account*`, `*history*`, `*conversation*`, `*cache*`, `*log*`, `*trajectory*`, `*install*`, `*.db`, `*.sqlite*`, `*.sock`) evaluated against every relative path component.
- Symlink containment and hardening (H1):
  - Rejects destination root and parents (`p_dir`, `p_home`, `target_gemini`) if any component is a symlink before any `mkdir`, `chmod`, traversal, or write.
  - Rejects source root (`real_gemini_dir`) if it is a symlink.
  - Skips in-tree source symlinks.
  - Rejects destination intermediate directories and file targets that are symlinks.
  - Revalidates complete profile layout under `.sync` file lock.
- Atomic updates: copies via private temporary file created with mode `0o600` at open time (`os.open(..., os.O_CREAT | os.O_WRONLY | os.O_TRUNC, 0o600)`) and `os.replace` under `.sync` lock.
- External profiles bypass synchronization.

### M2-S3: Round-Robin Selector
- Implemented `select_profile(explicit_name, now)` in `src/magy/routing.py`.
- Persistent cursor stored at `<magy-state>/routing.json` under short file lock (`< 2ms`), serialized independently of process lifetimes.
- Automatically selects available profiles (`healthy` and `untested`), skipping disabled, auth-required, rate-limited, and quota-exhausted profiles until cooldown expiry.
- When cooldown expires, profiles become eligible again.
- Explicit selection uses the requested enabled profile and emits warnings to stderr if in cooldown.
- Monotonic `last_selected_at`: persists `max(now, meta.last_selected_at or 0.0)` under lock to prevent clock skews or concurrent selections from moving timestamps backward.
- Concurrent removal resilience: retries selection up to 3 times if a profile is removed between cursor selection and record.
- Verified across multiple concurrent OS processes using a `multiprocessing.Barrier` with exact round-robin distribution and no lost updates.
- If no profiles are available, raises `NoAvailableProfileError` detailing status and earliest cooldown remaining.
- `get_routing_status()` returns detailed routing status including `untested_profiles`.

### M2-S4: Health Signal Classification & Credential Redaction
- Implemented `classify_run_health(exit_code, stdout, stderr, log_content)` in `src/magy/agy.py`.
- Unique private per-run logs created at `<magy-state>/logs/<name>/<run_id>.log` whenever `update_health=True` (auth, run, and passthrough).
- Relative caller `--log-file` paths resolved against child `cwd`.
- Inspects bounded recent output:
  - Bounded binary seek from EOF up to 32 KiB for log files via `read_bounded_log_tail()`, decoding with `errors="replace"` to avoid `UnicodeDecodeError`.
- Per-stream chronological classification & conservative cross-stream precedence (M2):
  - Each stream is evaluated chronologically by position.
  - Cross-stream precedence follows conservative hierarchy: `auth-required > quota-exhausted > rate-limited > timeout`.
  - Newer stdout auth signals reliably take precedence over older log rate limit signals.
- Multi-unit retry parsing: picks the latest matched retry duration in text, supporting seconds (`s`, `sec`), minutes (`m`, `min`), and hours (`h`, `hr`).
- Credential redaction (H5): `sanitize_reason()` comprehensively redacts:
  - Authorization headers with any scheme (`Basic`, `Bearer`, etc.) while retaining scheme context (`Authorization: Basic [REDACTED]`).
  - Standalone Basic and Bearer tokens.
  - URL embedded credentials (`https://[REDACTED_USER]:[REDACTED_PASS]@host`) and query parameters (`?[REDACTED_QUERY]`).
  - Compound credential keys: `access_token`, `refresh_token`, `client_secret`, `apiKey`, `x-api-key`, `password`, `auth_token`.
  - Quoted JSON key-value pairs (`{"access_token": "[REDACTED]"}`).
  - User home paths and email addresses.
  - Text bounded to 120 characters.

### M2-S5: CLI Profile Commands
- Extended `src/magy/cli.py` with commands:
  - `magy profile add <name> [--current]`
  - `magy profile create <name>` (registers managed profile layout)
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

### M2-S6: Transparent Agy Passthrough & Execution Hardening
- Supports:
  - `magy --profile <name> -- <agy arguments...>`
  - `magy -- <agy arguments...>`
  - `magy <agy arguments...>`
- Direct terminal ownership: in non-capturing mode, stdin/stdout/stderr are inherited directly, preserving TTY status on all standard descriptors for interactive prompts and curses TUIs.
- Direct executable discovery & resolver protocol (H7, H8):
  - PATH candidate enumeration skips multicall and tool-manager shims (mise, asdf, etc.) to discover direct Agy binaries.
  - Optional generic `agy_resolver` command executes without a shell under caller environment on every launch, resolving current direct executable without caching.
  - Profile child environment sets synthetic `HOME` and derives profile-local POSIX XDG directories, preventing tool-manager bootstrap under profile home.
- Process-tree termination & signals (H4, M1):
  - Launches child processes in a dedicated process group (`process_group=0` on POSIX).
  - Forwarding handlers for SIGINT and SIGTERM propagate signals to the entire child process group.
  - On timeout, terminates process tree (`SIGTERM` escalating to `SIGKILL` after grace period), drains streams, and reaps direct children before releasing lifecycle lease.
  - Normalizes signal-derived exit codes to POSIX standard `128 + abs(signum)`.
- Strictly parses Magy options before `--`, rejecting unknown options.

### M2-S7: Re-review Remediation (H1-H5, M1-M3)
- **H1: Foreground Process Group Ownership & Signal Forwarding:**
  - `has_own_pgrp = (os.name != "nt") and capture_output`. Interactive executions connected to inherited terminal streams maintain Magy's process group, ensuring the child remains in the controlling terminal's foreground process group and preventing `SIGTTIN` stops.
  - Signal forwarding routes to `os.killpg` only when the child owns its process group; otherwise forwards directly to `proc.send_signal()`.
  - Added controlling-terminal PTY regression `test_pty_controlling_terminal_foreground_pgrp` using `os.setsid()` and `os.tcsetpgrp()`.
- **H2: Complete Process-Tree Termination & Descendant Cleanup:**
  - Added `_is_pgrp_alive(pgid)` to probe process group membership via `os.killpg(pgid, 0)`.
  - `_kill_process_tree` waits through the full grace period even if the direct child exits early, escalating to `SIGKILL` if descendant processes in the group remain alive.
  - Direct child is reaped via `proc.wait(timeout=1.0)` before releasing the lifecycle lease.
  - Added `test_run_in_profile_timeout_kills_descendant_ignoring_sigterm` asserting descendant death.
- **H3: Contained Skills Synchronization & Symlink Boundary Enforcement:**
  - Replaced direct `config/skills` symlinking with safe contained file synchronization.
  - Any existing symlink at `target_gemini/config/skills` is unlinked.
  - Skills within `real_gemini/config/skills` are copied file-by-file through the standard allowlist pipeline. Intermediate and target symlinks are strictly rejected and not traversed.
  - Added `test_sync_profile_settings_rejects_external_skills_symlink` and `test_sync_profile_settings_copies_contained_skills`.
- **H4: Comprehensive Provider-Prefixed Credential Redaction:**
  - Expanded `sanitize_reason()` regexes in `src/magy/agy.py` with `(?:[A-Za-z0-9_-]+[_-])?` prefix matching across all credential categories.
  - Redacts `OPENAI_API_KEY`, `GOOGLE_ACCESS_TOKEN`, `AWS_SECRET_ACCESS_KEY`, CLI flags, and JSON fields while preserving format and context.
  - Added leak assertions in `tests/test_health.py`.
- **H5: Native Windows Lifecycle Locking:**
  - Implemented `_lock_fd` and `_unlock_fd` using `kernel32.LockFileEx` (`LOCKFILE_EXCLUSIVE_LOCK`, `LOCKFILE_FAIL_IMMEDIATELY`) and `kernel32.UnlockFileEx` via `msvcrt.get_osfhandle`.
  - Fails closed (`NotImplementedError`) on unsupported platforms.
  - Added `test_lifecycle_lease_locking_helpers` in `tests/test_profiles.py`.
- **M1: Legacy Registry Incarnation Migration:**
  - `load_profiles()` automatically populates and persists missing `incarnation_id` values under the registry lock.
  - `remove_profile()` safely handles legacy records without incarnation IDs.
  - Added `test_remove_profile_legacy_registry_migration_and_removal`.
- **M2: Rejection of Unregistered Profiles in Auth and Run:**
  - `run_in_profile()` validates profile presence via `get_profile()` and raises `KeyError` if unregistered, preventing untracked home directory creation.
  - `cli.py` handles `KeyError` with clean user error messages and exit code 1.
  - Added `test_run_in_profile_unregistered_name_rejected` and CLI regressions in `tests/test_cli_m2.py`.
- **M3: No-Follow Directory-Descriptor Relative Settings Sync:**
  - Implemented `_open_descendant_dir_fd` and `_safe_copy_file_fd` using `dir_fd` and `O_NOFOLLOW` on supported platforms (POSIX).
  - Atomic replacement via temporary files (`0o600`, `O_EXCL`) in target directory descriptor with `os.rename(..., src_dir_fd=..., dst_dir_fd=...)`.
  - Added `test_sync_profile_settings_detects_symlink_component_during_traversal`.

## 2. Test Verification

| Test Suite | Result | Details |
| --- | --- | --- |
| `tests/test_registry.py` | PASS | Profile metadata, add/remove/enable/disable/reset-health, external profiles, duplicate add rejection, active run removal rejection, anti-resurrection, disable/enable cooldown preservation |
| `tests/test_settings_sync.py` | PASS | Allowlist copying, secret exclusion across all path components, source symlink skip, destination symlink rejection, destination root & source root symlink rejection (H1), dir_fd no-follow traversal (M3), external skills symlink rejection & contained skills copy (H3), atomic sync under lock |
| `tests/test_routing.py` | PASS | Round-robin order, cooldown skipping, concurrent cursor advancement, monotonic `last_selected_at` (M3), selection retry on concurrent removal (M3), true multi-process barrier distribution (M5), status reporting |
| `tests/test_health.py` | PASS | Success, auth-required, rate-limited with multi-unit retry parsing, latest retry match (M2), quota, timeout, stdout auth, latest-signal precedence, conservative cross-stream precedence (M2), bounded log tail seek, comprehensive OAuth/Basic/compound/provider-prefixed credential redaction (H4, H5) |
| `tests/test_cli_m2.py` | PASS | CLI subcommands, JSON outputs, passthrough routing, unknown option rejection, `--version` handling, missing profile arg rejection, equals syntax, misplaced subcommand rejection, unregistered profile rejection (M2) |
| `tests/test_profiles.py` | PASS | Isolation gates, symlink hardening, active lease removal blocking (H2), ABA incarnation protection (H3), rollback on registry failure (H3), process-tree timeout cleanup & descendant death (H2), normalized signal exit codes (M1), fake concurrency, CLI run/auth sync, unregistered profile rejection (M2), legacy registry migration (M1), Windows lifecycle lock helpers (H5) |
| `tests/test_tty.py` | PASS | PTY interactive prompt visibility before input, prompt response, TTY status on 0/1/2, redirected stdout scriptability, private log injection, relative caller log resolution, controlling-terminal foreground pgrp (H1) |
| `tests/test_storage.py` | PASS | File locks, permissions, atomic writes |
| `tests/test_doctor.py` | PASS | Storage and Agy diagnostics, direct executable reporting, resolver probe |
| `tests/test_discovery.py` | PASS | PATH enumeration skipping shims, resolver execution and error handling, explicit cmd validation |
| `tests/test_snapshot.py` | PASS | Metadata snapshot diffing |
| `tests/test_fake_agy.py` | PASS | Signal handling, error modes |

- `uv run ruff check .`: PASS (0 errors across whole repository).
- `uv run ruff format --check .`: PASS (46 files formatted).
- `uv run pytest`: 208 passed on Linux (Python 3.14.7).
- `uv run --python 3.11 ruff check .`: PASS.
- `uv run --python 3.11 pytest`: 208 passed on Linux (Python 3.11.16).
- `uv build`: PASS (built wheel and tarball).

