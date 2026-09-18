# M3: Durable MCP

## Goal

Expose headless Agy delegation through stdio MCP while sharing all profile,
selection, health, and launch behavior with `magy`.

## Run Lifecycle

```text
queued -> running -> completed
                  -> failed
                  -> timed_out
                  -> cancelled
```

Persist state before spawning a detached worker. Worker and Agy process must
survive MCP server restart.

## Steps

### M3-S1: Durable run store

Store each run under the Magy state root with private files for request, state,
stdout, stderr, and Agy log. Normal status output must omit prompt, raw command,
and credential paths.

Add optional idempotency keys. Reusing a key returns the existing run; identical
prompts without a key remain independent.

### M3-S2: Detached worker

Spawn a package worker after state persistence. The worker:

- Selects a profile through the M2 selector unless one was requested.
- Builds the managed profile environment.
- Runs Agy in `--print` mode.
- Uses JSON output when the installed capability supports it.
- Adds `--dangerously-skip-permissions` by default, with an explicit opt-out.
- Writes dedicated private output and log files.
- Updates run and profile health on every terminal path.

Use a new process session on POSIX and a new process group on Windows.

### M3-S3: Cancellation and timeout

Cancel the Agy process tree, not only its immediate parent. Use graceful
termination followed by bounded forced termination. Cancellation and timeout
must become durable terminal states even if process cleanup reports an error.

### M3-S4: MCP tools

Expose:

- `magy_run_start`
- `magy_run_wait`
- `magy_run_status`
- `magy_run_result`
- `magy_run_cancel`
- `magy_profiles`

`magy_run_start` accepts prompt, workspace, optional profile/model/agent/effort/
mode, timeout, sandbox, additional directories, auto-approval, and idempotency
key.

`magy_run_wait` must short-poll and return before typical MCP gateway timeouts.
`magy_run_result` must support bounded chunks with stable offsets.

### M3-S5: Failure guidance

Quota and rate-limit failures return `retryable=true` and may suggest another
currently selectable profile. They must not launch a replacement run. Replay is
an explicit caller decision because the first run may have performed side
effects.

### M3-S6: Harness examples

Document stdio configuration for Codex and OpenCode. Examples must reference an
installed `magy-mcp` command and avoid embedding secrets.

## Deliverables

- Durable run store and worker.
- Six MCP tools.
- Cross-platform cancellation.
- Bounded result retrieval.
- Codex and OpenCode configuration examples.

## Exit Criteria

- A run started through MCP completes after MCP server restart.
- Wait/status/result can be called from a fresh server process.
- Cancellation kills fake Agy descendants on all target OSes.
- Idempotency prevents duplicate start for a reused key.
- Quota failure updates shared profile health without automatic replay.
- MCP stdout contains protocol frames only.

## Review Focus

- State is persisted before process spawn.
- Worker terminal paths always finalize state.
- Process-tree cancellation cannot target unrelated reused PIDs.
- Result chunk boundaries are deterministic.
- Tool descriptions disclose default auto-approval risk.
