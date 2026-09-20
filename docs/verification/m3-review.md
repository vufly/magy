# Milestone 3 Final Review

## Decision

**Pass with native-platform follow-up.** All reproduced Linux M3 durability,
cancellation, idempotency, process-safety, MCP contract, privacy, and result-chunking
defects are fixed. The full suite passes on Python 3.11 and 3.14, stdio traffic is
validated as JSON-RPC only, a detached worker survives an actual MCP server-process
restart, and package build succeeds.

Milestone 4 may proceed. Native Windows and macOS process-tree, ACL, and transport
verification remains part of M4 hardening.

## Findings

No remaining release-blocking findings were identified in the reviewed Linux M3
scope.

## Resolved Findings

### Durable state and idempotency

- Idempotency lookup, run creation, and key reservation execute under one lock.
  Concurrent starts produce one durable run and one worker winner.
- Worker claim is an atomic `queued -> running` transition. Duplicate workers and
  every non-queued state are rejected without replay.
- Worker spawn and identity publication failures become controlled durable failures.
  A spawned worker is terminated before failure is reported.
- Stale queued/running workers, zombie workers, and unpublished bootstrap failures are
  reconciled by fresh server processes.
- Cancellation, invalid configuration, and spawn failure cannot overwrite each
  other's terminal states.

### Cancellation and process safety

- Child launch and PID/create-time publication occur while holding the run lock, so
  cancellation cannot miss a newly launched child.
- Process identity uses PID creation time plus per-run environment markers, avoiding
  reused-PID signalling.
- Cleanup discovers recursive children and marker-tagged detached/double-forked
  descendants, refreshes during graceful termination, and escalates with bounded
  forced termination.
- Queued/pre-launch workers are cancelled safely even when no child PID exists.
- `cancelling` is restart-recoverable; fresh status/wait calls finish cleanup and
  persist `cancelled`.
- Cleanup completes before `completed` becomes visible. Failed cleanup is represented
  by `cleanup_pending` and retried by later status calls.
- Cancellation and timeout remain durable terminal states even if cleanup initially
  fails.
- Detached worker processes are reaped by a background waiter in the server process.

### Shared profile and launch behavior

- Worker profile selection uses the M2 selector for explicit and automatic profiles.
- Selected incarnation and enabled state are verified again under the shared profile
  lease before synchronization or launch.
- Malformed configuration fails closed rather than falling back to PATH discovery.
- Auto-approval and additional-directory flags are emitted only when installed Agy
  capabilities support them.

### MCP contract and privacy

- Tool schemas declare `additionalProperties=false`, and runtime validation rejects
  unknown fields before SDK coercion.
- Security-sensitive booleans, numbers, integer offsets/limits, and additional
  directory arrays are validated strictly.
- Tool failures use sanitized `ToolError` responses with MCP `isError=true`; raw
  exceptions and filesystem paths are not returned.
- Tool outputs are typed structured models.
- `magy_profiles` returns only documented health/routing fields, excluding home paths,
  incarnation IDs, and internal bookkeeping.
- Wait polling is asynchronous and does not hold a worker thread for the full wait.

### Result retrieval

- Result chunks enforce a 4-byte minimum and 1 MiB maximum in schema and runtime.
- Byte offsets remain deterministic and chunks never split a UTF-8 character.
- Invalid offsets, invalid UTF-8, missing output files, and unreadable output produce
  sanitized MCP errors rather than empty successful responses.
- EOF is not terminally exposed until cancellation or descendant cleanup completes.

## Verification

| Command or check | Result |
| --- | --- |
| `uv run pytest -q` | PASS, 281 tests on Linux/Python 3.14.7 |
| `uv run --python 3.11 pytest -q` | PASS, 281 tests on Linux/Python 3.11.16 |
| `uv run ruff check .` | PASS |
| `uv run --python 3.11 ruff check .` | PASS |
| `uv run ruff format --check .` | PASS, 54 files formatted |
| `uv build` | PASS, wheel and source distribution built |
| Concurrent same-key starts | PASS, one run and one winner |
| Cancel during child PID publication | PASS |
| Queued/pre-launch worker cancellation | PASS |
| Descendant and double-fork marker cleanup | PASS |
| Reused/unmarked PID rejection | PASS |
| Cancellation/timeout cleanup failure recovery | PASS |
| Worker/profile incarnation race | PASS |
| Strict unknown-field and type validation | PASS |
| Sanitized MCP `isError` behavior | PASS |
| Caller-safe profile response | PASS |
| UTF-8 chunk reconstruction and maximum bound | PASS |
| Real MCP subprocess restart with detached worker | PASS |
| Raw stdio output JSON-RPC validation | PASS |

## Residual Risks

- Native Windows process-tree behavior, file ACL privacy, and reparse-point races were
  not executed in this Linux review.
- Native macOS process environment access and detached descendant discovery were not
  executed.
- Long-term run retention and cleanup policy remains an M4 operational-hardening item.

These are explicit M4 verification items rather than Linux M3 gate blockers.
