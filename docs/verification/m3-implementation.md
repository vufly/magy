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
  - Uses `psutil` recursive process discovery plus `MAGY_RUN_ID` environment markers.
  - Verifies PID creation time and run marker before signalling stored identities.
  - Graceful termination is followed by bounded forced termination with descendant rescans.
  - Detects detached, double-forked, and `setsid()` descendants through inherited run markers.
- Timeout execution:
  - If execution exceeds `request.timeout`, process tree is terminated, run transitions to `timed_out`, and profile health transitions to `timeout`.
- Cancellation via `cancel_run`:
  - Uses restart-recoverable `cancelling` state while process cleanup is active.
  - Terminates queued workers or child process trees without trusting stale PIDs.
  - Sets durable terminal state `cancelled` after cleanup.
  - Subsequent cancellations return terminal status without re-running cancellation logic.

### Final Independent Review Remediation

- Made idempotency reservation and worker claim atomic.
- Made child spawn and PID/create-time publication cancellation-safe under run lock.
- Added stale worker reconciliation and controlled spawn/bootstrap failure states.
- Added restart-recoverable cancellation and durable cleanup retry metadata.
- Added typed strict MCP schemas, sanitized `ToolError` responses, and safe profile output.
- Added bounded UTF-8-safe result chunks with stable byte offsets.
- Added real stdio server restart, stdout protocol-purity, concurrency, PID-reuse,
  descendant cleanup, and lifecycle-race regressions.

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
| `tests/test_runs.py` | PASS (34 tests) | Atomic idempotency and claims, durable reconciliation, bounded UTF-8 chunks, cancellation recovery, PID identity, detached descendants, and cleanup retry |
| `tests/test_worker.py` | PASS (13 tests) | Success/failure health, profile incarnation, capability gating, timeout/cancellation races, descendant cleanup, and terminal ordering |
| `tests/test_mcp_server.py` | PASS (12 tests) | Strict schemas, sanitized errors, typed tools, safe profiles, stdio protocol purity, and real server-process restart survival |
| Full test suite (`pytest`) | PASS (281 tests) | All M0, M1, M2, and M3 tests passing on Linux (Python 3.14.7) |
| Min Python suite (`--python 3.11`) | PASS (281 tests) | All tests passing on Linux (Python 3.11.16) |
| Ruff lint (`ruff check .`) | PASS | 0 errors across whole repository |
| Ruff format (`ruff format --check .`) | PASS | 54 files formatted |
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

- Detached process identity and descendant discovery use `psutil` creation times,
  recursive children, and inherited `MAGY_RUN_ID` environment markers.
- Full cross-platform verification for native Windows process group creation and macOS job control is scheduled for Milestone 4 (Hardening and Release).
