# Milestone 3 Implementation: Durable MCP

## 1. Implementation Summary

Milestone 3 delivers headless Antigravity (`agy`) delegation through an MCP stdio
server (`magy-mcp`), complete with a durable run store, detached worker execution,
cross-process cancellation and timeouts, failure guidance without automatic replay,
and six core MCP tools.

### M3-S1: Durable Run Store
- Implemented `RunRequest`, `RunState`, `RunStatus`, and `RunResult` models in `src/magy/runs.py`.
- Run layout persisted under `<magy-state>/runs/<run_id>`:
  - `request.json`: request metadata and options (`0o600`).
  - `state.json`: run lifecycle state, timing, PIDs, exit code, and health (`0o600`).
  - `stdout.log`: streaming stdout captured directly from child process (`0o600`).
  - `stderr.log`: streaming stderr captured directly from child process (`0o600`).
  - `agy.log`: private injected Agy diagnostics and network log (`0o600`).
- Run directory permissions enforced as owner-only (`0o700`).
- Request and initial state (`queued`) are strictly persisted *before* spawning the detached worker process.
- Caller-facing `RunStatus` strictly omits prompts, raw CLI arguments, and credential or system log paths.
- Idempotency support:
  - Idempotency mappings persisted under `<magy-state>/idempotency.json` under file lock.
  - Providing an existing `idempotency_key` returns the existing run status without duplicating execution.
  - Identical prompts without idempotency keys run as independent runs.

### M3-S2: Detached Worker Execution
- Implemented detached worker in `src/magy/worker.py` (executable via `python -m magy.worker <run_id>` or `magy worker <run_id>`).
- Detached process spawning in `spawn_detached_worker`:
  - POSIX: spawns using `start_new_session=True` (`os.setsid()`), detached from parent terminal and MCP server lifecycle.
  - Windows: spawns using `CREATE_NEW_PROCESS_GROUP | DETACHED_PROCESS`.
  - Survives MCP server restarts.
- Worker execution flow:
  1. Profile selection: uses explicitly requested profile or falls back to M2 round-robin selector (`select_profile()`).
  2. Acquires shared lifecycle lease (`acquire_profile_lease`) to coordinate with profile mutation/removal.
  3. Synchronizes allowlisted settings (`sync_profile_settings`) and prepares isolated environment (`build_profile_env`).
  4. Discovers direct `agy` executable via `resolve_agy_executable`.
  5. Inspects capability flags via `get_agy_capabilities` in `src/magy/agy.py`:
     - Emits `--output-format json` when supported.
     - Adds `--dangerously-skip-permissions` by default (disclosed in MCP tool descriptions), with explicit opt-out via `auto_approval=False`.
     - Supports `--add-dir`, `--model`, `--agent`, `--effort`, `--mode`, and `--sandbox`.
     - Injects `--log-file <run_dir>/agy.log`.
  6. Launches child process in dedicated process group (`process_group=0` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows) with streams redirected to log files.
  7. Tags environment with `MAGY_RUN_ID=<run_id>` for Linux proc marker scanning.
  8. Updates run and profile health on all terminal paths (`completed`, `failed`, `timed_out`, `cancelled`).

### M3-S3: Cancellation and Timeout
- Cross-process tree termination implemented in `_terminate_pid_tree` (`src/magy/runs.py`):
  - Graceful `SIGTERM` sent to child process group (`os.killpg` on POSIX, `taskkill /F /T` on Windows).
  - Grace period wait (1.0s).
  - Bounded escalation to `SIGKILL` if processes remain alive.
  - On Linux, checks `/proc/<pid>/environ` for `MAGY_RUN_ID=<run_id>` marker to avoid targeting recycled/reused PIDs.
- Timeout execution:
  - If execution exceeds `request.timeout`, process tree is terminated, run transitions to `timed_out`, and profile health transitions to `timeout`.
- Cancellation via `cancel_run`:
  - Terminates child process tree and worker process.
  - Sets durable terminal state `cancelled`.
  - Subsequent cancellations return terminal status without re-running cancellation logic.

### M3-S4: MCP Tools
- Implemented MCP Server in `src/magy/mcp_server.py` using `mcp.server.mcpserver.MCPServer`:
  - `magy_run_start`: Initiates detached Agy run. Discloses default `auto_approval` security considerations.
  - `magy_run_wait`: Short-polls run status up to clamped timeout (max 60s) to prevent MCP gateway timeouts.
  - `magy_run_status`: Returns caller-safe status omitting prompts, commands, and secrets.
  - `magy_run_result`: Retrieves bounded chunks with stable byte offsets, returning `content`, `offset`, `next_offset`, `eof`, and `is_json`.
  - `magy_run_cancel`: Cancels run and terminates process tree.
  - `magy_profiles`: Returns registered profiles, health, cooldowns, and round-robin routing state.
- Stdout protocol hygiene:
  - Standard output is reserved strictly for JSON-RPC MCP frames; all internal logging and errors write to `stderr`.

### M3-S5: Failure Guidance
- Quota and rate-limit failures return `retryable=True` in `RunStatus`.
- Evaluates registered profiles and suggests an alternate available profile in `suggested_profile`.
- Does not automatically retry or replay started tasks (leaving replay decisions to callers to avoid unexpected side effects).

### M3-S6: Harness Examples
- Documented stdio configuration for Codex and OpenCode in `docs/mcp-configuration.md`.
- Examples reference installed `magy-mcp` binary without hardcoded tokens or secrets.

---

## 2. Test Verification

| Test Suite | Result | Details |
| --- | --- | --- |
| `tests/test_runs.py` | PASS (10 tests) | Run persistence before spawn, idempotency mapping and reuse, prompt omission in status, bounded result chunks with offsets, JSON detection, parameter validation, cancellation, wait short-poll, retryable alternate suggestion |
| `tests/test_worker.py` | PASS (5 tests) | Successful worker run, round-robin profile selection, failure classification and health update, timeout termination, missing executable failure handling |
| `tests/test_mcp_server.py` | PASS (6 tests) | Tool listing and description checks, run start and status, run result and wait, run cancel, profile listing, server restart survival (fresh server recovering state from disk) |
| Full test suite (`pytest`) | PASS (243 tests) | All M0, M1, M2, and M3 tests passing on Linux (Python 3.14.7) |
| Min Python suite (`--python 3.11`) | PASS (243 tests) | All tests passing on Linux (Python 3.11.16) |
| Ruff lint (`ruff check .`) | PASS | 0 errors across whole repository |
| Ruff format (`ruff format --check .`) | PASS | 52 files formatted |
| Package build (`uv build`) | PASS | Wheel and source tarball built successfully |
| Tool install (`uv tool install --force .`) | PASS | Installed `magy` and `magy-mcp` globally |

---

## 3. Design Deviations and Rationale

- **MCP SDK Version**: The environment uses `mcp>=2.2.0`, where `FastMCP` was migrated to `mcp.server.mcpserver.MCPServer`. Implemented `magy-mcp` using `MCPServer` directly to align with `mcp>=2.2.0` while maintaining full compatibility with Python 3.11 and 3.14.
- **Async Test Harness**: Test functions run `asyncio.run()` directly within standard `pytest` fixtures, avoiding external plugin dependencies (`pytest-asyncio`) and ensuring tests run portably across test runners.

---

## 4. Security-Sensitive Paths Touched

- `<magy-state>/runs/<run_id>/request.json` (mode `0o600`)
- `<magy-state>/runs/<run_id>/state.json` (mode `0o600`)
- `<magy-state>/runs/<run_id>/stdout.log` (mode `0o600`)
- `<magy-state>/runs/<run_id>/stderr.log` (mode `0o600`)
- `<magy-state>/runs/<run_id>/agy.log` (mode `0o600`)
- `<magy-state>/idempotency.json` (mode `0o600`)

---

## 5. Known Limitations & Next Milestone Prerequisites

- Detached process PID marker verification uses `/proc/<pid>/environ` on Linux; on non-Linux POSIX platforms, process group and process existence checks are used.
- Full cross-platform verification for native Windows process group creation and macOS job control is scheduled for Milestone 4 (Hardening and Release).
