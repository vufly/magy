# MCP Server Review and Follow-up Handoff

## Overview

This document reviews [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py),
the durable run implementation, and the Zellij-backed headful launch path.

- **Primary files:** [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py),
  [`src/magy/headful.py`](../../src/magy/headful.py)
- **Supporting files:** [`src/magy/runs.py`](../../src/magy/runs.py),
  [`src/magy/worker.py`](../../src/magy/worker.py),
  [`src/magy/profiles.py`](../../src/magy/profiles.py),
  [`src/magy/routing.py`](../../src/magy/routing.py)
- **Tests:** [`tests/test_mcp_server.py`](../../tests/test_mcp_server.py),
  [`tests/test_headful.py`](../../tests/test_headful.py),
  [`tests/test_runs.py`](../../tests/test_runs.py)
- **Review date:** 2026-09-26
- **Gate decision:** **PASS WITH FOLLOW-UP**

## Executive Summary

Magy's MCP server exposes seven tools:

- `magy_run_start`
- `magy_run_headful`
- `magy_run_wait`
- `magy_run_status`
- `magy_run_result`
- `magy_run_cancel`
- `magy_profiles`

The durable execution path has sound process isolation, bounded result reads,
restart-safe state, cancellation, and sanitized public status responses. The
headful path preserves profile isolation while opening Agy in an active Zellij
session. Test coverage is broad, and no release-blocking defect was found.

Follow-up work should focus on one documented trust-boundary decision, one MCP
error-reporting improvement, and several low-severity contract cleanups.

## Findings

### Medium: Workspace Trust Boundary Needs an Explicit Contract

**Locations:** [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py),
[`src/magy/headful.py`](../../src/magy/headful.py),
[`src/magy/worker.py`](../../src/magy/worker.py)

Both execution tools default `auto_approval` to `True`, which intentionally adds
`--dangerously-skip-permissions`. They also accept caller-selected `workspace`
and `additional_dirs` values. This makes Magy a trusted local execution
capability with the permissions of the user running `magy-mcp`.

This behavior is intentional, not an implementation vulnerability by itself.
However, configuration documentation should state the trust boundary directly:
any client allowed to call these tools can request autonomous work in arbitrary
user-accessible directories.

Recommended follow-up:

1. Document this capability in the MCP security notes.
2. Canonicalize and validate `workspace` and `additional_dirs` consistently for
   both headless and headful runs.
3. Decide whether Magy's config, state, and managed profile roots should be
   rejected as explicit workspaces or additional directories. If unrestricted
   paths remain intentional, document that decision instead of adding an
   implicit allowlist.

### Medium: Safe Client Validation Errors Are Hidden

**Location:** [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py)

`_raise_tool_error` converts implementation exceptions into fixed public
messages. This protects private paths, but it also hides actionable validation
errors such as:

- whitespace-only prompts
- invalid idempotency key lengths
- UTF-8 offsets that do not point to a character boundary

The current behavior is safe but unnecessarily opaque. Preserve only a small,
explicit allowlist of known validation messages. Do not expose arbitrary
`ValueError` text or infer safety from the absence of `/` or `\`, because those
checks do not reliably detect paths or secrets.

### Low: `call_tool` Assumes `arguments` Is a Mapping

**Location:** [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py)

`StrictMCPServer.call_tool` raises `TypeError` when called programmatically with
`arguments=None`. This was reproduced with:

```python
await create_mcp_server().call_tool("magy_profiles", None)
```

Normal MCP transport calls are expected to provide an arguments object, so this
is a robustness issue rather than a transport-level outage. Normalize `None` to
`{}` before custom validation if direct server calls are supported.

### Low: Expired Cooldown Metadata Has Ambiguous Semantics

**Locations:** [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py),
[`src/magy/routing.py`](../../src/magy/routing.py)

`magy_profiles` can return `available=True` while preserving an expired
`cooldown_until` and its reason. Meanwhile, `routing.cooldown_profiles` counts
only active cooldowns. The values are internally consistent if profile fields
represent stored history, but clients may interpret them as active state.

Choose and test one contract:

- clear expired cooldown fields in public profile responses, or
- preserve them and document that `available` and `cooldown_profiles` determine
  current availability while cooldown fields may describe previous state.

### Low: Run ID Schemas Do Not Advertise Basic Bounds

**Location:** [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py)

Run tools accept unconstrained `run_id: str` schemas even though storage applies
stricter validation. Adding `min_length` and a conservative `max_length` would
reject malformed input earlier and improve generated MCP schemas.

### Low: Profile Status Loads Registry Twice

**Locations:** [`src/magy/mcp_server.py`](../../src/magy/mcp_server.py),
[`src/magy/routing.py`](../../src/magy/routing.py)

`handle_magy_profiles` calls `load_profiles`, then `get_routing_status` loads the
registry again. This is minor duplicated I/O and permits a small consistency
window between profile details and aggregate counts. Let `get_routing_status`
accept a preloaded profile mapping if a single-snapshot response is desired.

## Reviewed and Rejected Findings

### Wait Timeout Clamping Is Intentional

`magy_run_wait` explicitly documents and implements a `0.1` to `60` second
clamp to avoid MCP gateway timeouts. Rejecting out-of-range values would change
the existing contract. Schema bounds may be added for discoverability only if
the implementation continues to clamp values or the behavior change is made
deliberately.

JSON-RPC does not support NaN or Infinity as standard JSON numbers, so those
values are not a primary transport concern. Direct Python callers may still
benefit from finite-number validation.

### Tool Schema Reconstruction Is Not Worth Private API Coupling

`StrictMCPServer.call_tool` uses `list_tools` during validation. Replacing this
with a private MCP server manager API would save negligible work for seven tools
while coupling Magy to unstable internals. Keep the current implementation
unless profiling identifies meaningful overhead or the MCP library exposes a
public lookup API.

## Test Follow-up

Existing lower-level tests already cover UTF-8 boundary detection and terminal
cancellation idempotency. Add MCP-level tests only where they verify public
tool behavior:

| ID | Area | Scenario | Expected behavior |
| --- | --- | --- | --- |
| T1 | Server | `call_tool("magy_profiles", None)` | Normalizes to empty arguments or returns a deliberate `ToolError` |
| T2 | Paths | Invalid, non-directory, and protected workspace/additional paths | Follows documented trust-boundary policy consistently in both execution modes |
| T3 | Start | Whitespace prompt and invalid idempotency key | Returns safe, actionable validation errors |
| T4 | Result | Offset in middle of UTF-8 character | MCP response reports a safe boundary error |
| T5 | Profiles | Expired cooldown | Matches selected public cooldown contract |
| T6 | Run tools | Empty or oversized run ID | Rejected by generated schema or a clear tool error |
| T7 | Main | MCP server startup failure | Logs to stderr and returns exit code `1` |

## Recommended Order

1. Document and decide the workspace trust boundary.
2. Add explicit safe validation-error mapping.
3. Normalize `arguments=None` if programmatic calls remain supported.
4. Resolve expired cooldown response semantics.
5. Add run ID schema bounds and focused MCP contract tests.
6. Optionally remove the duplicate profile registry read.

Verification commands:

```bash
uv run pytest tests/test_mcp_server.py tests/test_headful.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```
