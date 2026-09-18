# Milestone 0 Implementation Report: Foundation

## 1. Milestone and Commit/Worktree State

- **Milestone:** M0 (Foundation)
- **Base Commit:** `cc6903d` ("Add initial design, planning, and handoff documentation")
- **Worktree State:** Clean baseline with newly added files:
  - `.github/workflows/ci.yml`
  - `.gitignore`
  - `.python-version`
  - `pyproject.toml`
  - `uv.lock`
  - `src/magy/__init__.py`
  - `src/magy/py.typed`
  - `src/magy/storage.py`
  - `src/magy/config.py`
  - `src/magy/agy.py`
  - `src/magy/cli.py`
  - `src/magy/mcp_server.py`
  - `src/magy/testing/__init__.py`
  - `src/magy/testing/fake_agy.py`
  - `tests/conftest.py`
  - `tests/test_storage.py`
  - `tests/test_discovery.py`
  - `tests/test_fake_agy.py`
  - `tests/test_doctor.py`

## 2. Completed Step IDs

- **M0-S1: Package scaffold**
  - Configured `pyproject.toml` with `src/` layout, Python `>=3.11` compatibility, console entry points `magy = "magy.cli:main"` and `magy-mcp = "magy.mcp_server:main"`.
  - Added runtime dependencies (`mcp`, `filelock`, `platformdirs`) and development tools (`pytest`, `ruff`).
  - Added GitHub Actions workflow `.github/workflows/ci.yml` executing linting and pytest matrix across Linux, macOS, and Windows for Python 3.11 and 3.14.
- **M0-S2: Path and executable discovery**
  - Implemented OS-native config, data, and state roots using `platformdirs` with environment variable overrides (`MAGY_CONFIG_DIR`, `MAGY_DATA_DIR`, `MAGY_STATE_DIR`) in `src/magy/config.py`.
  - Implemented discovery order in `src/magy/agy.py`:
    1. `MAGY_AGY_CMD`
    2. Configured executable path (`config.json`)
    3. `agy` on `PATH`
  - Implemented `magy doctor` reporting executable path, version, platform, storage paths, configuration error status, and prerequisites without reading credential contents.
- **M0-S3: Fake Agy harness**
  - Created executable test harness in `src/magy/testing/fake_agy.py` and `tests/conftest.py`.
  - Supports invocation recording (args, selected environment variables), simulated status codes and messages (success, auth error, quota error, rate limit, timeout, custom), sleep until cancellation, and spawning child processes for process-tree cancellation verification.
  - Test harness enforces test isolation in `tests/conftest.py`, ensuring tests never inadvertently discover or run real `agy`.
- **M0-S4: Private and atomic storage helpers**
  - Created `src/magy/storage.py` providing `atomic_write_json`, `read_json`, `update_json`, and `get_lock`.
  - Uses temp-file write loop handling partial writes, fsync, atomic replace (`os.replace`), and parent directory fsync guarded by file locks.
  - Enforces `0o700` permissions on directories and `0o600` on files on POSIX platforms.
  - Implemented strict identifier validation with full-string matching (`\A...\Z` with `fullmatch`) rejecting newlines, carriage returns, control characters, path separators, `..`, `.`, and platform-reserved device names (`CON`, `NUL`, etc.).

## 3. Review Remediation (Post M0-Review)

Remediated findings from `docs/verification/m0-review.md`:
- **F1 (Identifier validation accepts trailing newline):** Changed `IDENTIFIER_PATTERN` to `\A[a-zA-Z0-9_-]+\Z` and switched to `IDENTIFIER_PATTERN.fullmatch(name)`. Added tests for trailing/embedded newlines, carriage returns, tabs, null bytes, and control characters in `tests/test_storage.py`.
- **F2 (Partial writes in atomic replacement):** Updated `atomic_write_json` to write in a loop until all bytes are written, fsync the file descriptor before closing, and fsync the parent directory on POSIX after `os.replace`. Added `test_atomic_write_partial_writes` monkeypatching `os.write` to chunk at 3 bytes per call.
- **F3 (Invalid config handling in doctor):** Added validation in `MagyConfig.__post_init__` requiring `agy_cmd` to be a non-empty string or null. Implemented `load_config_result()` capturing parse errors, lock timeouts, permission errors, and invalid schema, and surfaced bounded `config_error` in `magy doctor` (both text and JSON outputs). Added tests in `tests/test_doctor.py` covering malformed JSON, invalid types, empty strings, and lock timeouts.
- **G1 (Multi-process writer test):** Added `test_atomic_write_concurrent_processes` using Python's `spawn` multiprocessing context across 4 processes performing atomic locked updates with `update_json`.
- **G2 (Child process test clarification):** Renamed test to `test_fake_agy_spawn_child_process` and updated docstrings to clarify child process spawning and PID tracking for future M3 tree cancellation.
- **R1 (Storage-root error boundary):** Added `create: bool = True` parameter to `get_config_dir`, `get_data_dir`, and `get_state_dir`. Included config path resolution and existence checks within the `load_config_result()` `try...except` block to catch `PermissionError` and `OSError`. Wrapped storage-root directory access in `collect_diagnostics()` with a safe error boundary so inaccessible storage roots report bounded prerequisite errors without raising tracebacks. Added regression tests in `tests/test_doctor.py` for config path resolution errors, and config, data, and state root permission and OS error paths in both text and JSON modes.

## 4. Commands Run and Pass/Fail Results

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Code style and linting passed with 0 errors on Python 3.14. |
| `uv run pytest` | PASS | 68 passed in 7.22s on Python 3.14.7. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python lint check passed. |
| `uv run --python 3.11 pytest` | PASS | 68 passed in 7.28s on Python 3.11.16. |
| `uv build` | PASS | Successfully built wheel and sdist. |
| `uv run magy doctor` | PASS | Discovered Agy 1.2.6 on PATH; all checks OK. |
| `uv run magy doctor --json` | PASS | Valid structured JSON emitted with healthy=true. |
| R1 permission reproduction | PASS | Exit code 1, bounded error message, zero traceback. |
| `uv run magy --help` | PASS | Subcommand help displayed properly. |
| `uv run magy-mcp` | PASS | Server entry point stub executed cleanly. |

## 5. Manual Actions Still Required

- None for M0.
- Manual execution will be required during M1 to authenticate two distinct live Google/Agy accounts.

## 6. Security-Sensitive Paths Touched

- Platformdirs storage directories evaluated:
  - Config: `~/.config/magy`
  - Data: `~/.local/share/magy`
  - State: `~/.local/state/magy`
- Storage helper enforces private mode (`0o700` / `0o600`).
- No credential or OAuth token files were created, inspected, or copied.
- Real `~/.gemini/antigravity-cli` was untouched by tests.

## 7. Known Limitations and Next Milestone Prerequisites

- **Limitations:** Production profile lifecycle, safe setting synchronization, round-robin cursor, and MCP tools are not yet implemented (by design per M0 scope).
- **Next Milestone Prerequisites:** M1 (Profile Isolation Gate) requires:
  - Managed profile launcher setting profile-specific home environment variables (`HOME`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `MAGY_REAL_HOME`, `AGY_CLI_DISABLE_AUTO_UPDATE`).
  - Temporary CLI surface to authenticate profiles visibly.
  - Verification that two separate managed profiles maintain distinct authenticated states without polluting `~/.gemini`.
