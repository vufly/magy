# Magy MCP Server Configuration Guide

This document outlines how to configure `magy-mcp` for headless delegation in
coding assistants like Codex and OpenCode using stdio transport.

`magy-mcp` delegates tasks to `agy` across isolated profiles with automatic
round-robin routing, cooldown handling, and health signal classification.

## Prerequisites

Install `magy` and `magy-mcp` globally or within your environment:

```bash
uv tool install magy
# or within a repo checkout
uv tool install --force .
```

Verify that `magy-mcp` is available on your `PATH`:

```bash
magy-mcp --help
```

---

## 1. Codex Configuration

To add `magy` as a tool provider in Codex, add the following configuration to
your Codex configuration file (e.g. `~/.codex/config.json` or
`~/.config/codex/config.json`):

```json
{
  "mcpServers": {
    "magy": {
      "command": "magy-mcp",
      "args": []
    }
  }
}
```

If `magy-mcp` is installed in a specific environment (e.g. via `uv`), specify
the absolute binary path:

```json
{
  "mcpServers": {
    "magy": {
      "command": "/home/user/.local/bin/magy-mcp",
      "args": []
    }
  }
}
```

---

## 2. OpenCode Configuration

For OpenCode, add `magy` to your tools or MCP servers block (e.g. in
`~/.config/opencode/config.json` or project-level `.opencode/mcp.json`):

```json
{
  "mcp": {
    "servers": {
      "magy": {
        "command": "magy-mcp",
        "args": [],
        "type": "stdio"
      }
    }
  }
}
```

---

## 3. Available Tools

Tools are registered with un-prefixed names. When loaded into clients that namespace tools by server name (such as OpenCode prefixing `magy_run_start` or Claude Code using `mcp__magy__run_start`), tool names remain clean and free from duplicate prefixes (e.g. `magy_magy_*`). Legacy `magy_*` names remain supported via server-side aliases.

| Tool | Purpose | Key Parameters |
| --- | --- | --- |
| `run_start` | Start a detached, asynchronous Agy run | `prompt` (required), `workspace`, `profile`, `timeout`, `auto_approval`, `idempotency_key` |
| `pane_start` | Open interactive Agy in a split pane in active Zellij session or custom multiplexer; returns pane metadata immediately | `prompt` (required), `workspace`, `profile`, `model`, `agent`, `effort`, `mode`, `sandbox`, `additional_dirs`, `auto_approval`, `mux`, `mux_cmd` |
| `run_wait` | Short-poll until a run completes or times out | `run_id`, `timeout` (default 20s, max 60s) |
| `run_status` | Query safe run status | `run_id` |
| `run_result` | Retrieve bounded output chunks with stable offsets | `run_id`, `offset` (default 0), `limit` (default 64 KiB) |
| `run_cancel` | Terminate a running or queued process tree | `run_id` |
| `profiles` | Query registered profiles, availability, and routing | None |
| `watch_start` | Start a watched non-interactive Agy run in a floating pane with Git snapshots | `prompt` (required), `workspace`, `profile`, `model`, `agent`, `effort`, `mode`, `sandbox`, `additional_dirs`, `auto_approval` (default False; True required for automated tool actions), `continue_watch_id`, `mux`, `mux_cmd` |
| `watch_status` | Query watched run status (`running`, `completed`, `failed`, `cancelled`) | `watch_id` (required) |
| `watch_wait` | Short-poll until a watched run completes, fails, or is cancelled | `watch_id` (required), `timeout` (default 20s, max 60s) |
| `watch_log` | Retrieve bounded execution log chunks (NDJSON events) for mid-run monitoring | `watch_id` (required), `offset` (default 0), `limit` (default 64 KiB) |
| `watch_cancel` | Cancel a watched run, terminate processes, close pane, and release repo lease | `watch_id` (required) |
| `watch_diff` | Retrieve bounded chunks of the net Git diff produced by the run | `watch_id` (required), `offset` (default 0), `limit` (default 64 KiB) |

> [!NOTE]
> `run_start` defaults to `auto_approval=True` (`--dangerously-skip-permissions`)
> to enable unattended headless delegation. Set `auto_approval=False` if tool
> actions require interactive approval. See [`docs/architecture/security.md`](../architecture/security.md) for details on trust boundaries.

`pane_start` requires OpenCode and its Magy MCP server to run inside an
active Zellij session (or provide `mux_cmd` for custom terminal multiplexers). It opens Agy in a right split and returns the pane ID;
live interaction and output remain in that pane, with no MCP wait/result
capture. It also defaults to `auto_approval=True` and accepts
`auto_approval=False` for interactive approvals in the pane. If no active
Zellij session is available and no custom mux is configured, the tool fails instead of switching to headless
execution.

---

## 4. Human-in-the-Loop Watched Execution Workflow (Terminal UI & Git Snapshots)

The watched workflow (`watch_*`) allows an orchestrating agent to delegate tasks to Agy in a visible floating terminal pane, stream NDJSON output for real-time monitoring, preserve conversation context across follow-up iterations, and capture a clean Git patch diff excluding pre-existing repository dirt.

### Prerequisites

- An active Zellij session (`ZELLIJ` and `ZELLIJ_SESSION_NAME` present in environment) or custom `mux_cmd`.
- `zellij` and `git` binaries available on `PATH`.
- Target workspace must reside within an existing Git repository with a `HEAD` commit.

### Execution Model

1. **Non-Interactive Floating Pane**: Unlike `pane_start` (which opens an interactive REPL in a split pane), watched runs launch `agy --print <prompt> --output-format stream-json` in a floating terminal pane. The user observes thinking and execution in real time as human-readable events rendered in the pane, while stderr is inherited live. The process exits automatically upon completion, leaving process control to the orchestrating agent.
2. **Tool Permissions and Auto-Approval**: In `stream-json` mode, Agy operates non-interactively and does not accept interactive approval prompts in the terminal pane. While `watch_start` defaults to `auto_approval=False`, setting `auto_approval=True` is required so that Agy passes `--dangerously-skip-permissions`; without it, tool actions requiring permissions are denied.
3. **Mid-Run Monitoring**: Terminal events are recorded as raw NDJSON in `watches/<watch-id>/pty.log` while rendered in human-readable form in the pane. The harness agent can read chunked logs mid-run with `watch_log` to observe progress without blocking.
4. **Git Baseline Snapshots & Dirt Exclusion**: Prior to launching the pane, Magy creates a temporary Git index tree snapshot of tracked, staged, unstaged, and non-ignored untracked files without altering the user's working tree or index. When execution finishes, a final snapshot is taken and net diff computed:
   ```bash
   git diff --no-ext-diff --no-textconv --binary <baseline-tree> <final-tree>
   ```
   All pre-existing untracked files, staged changes, and unstaged modifications are excluded from the returned patch.
5. **Repository Concurrency Lease**: To prevent interleaving changes from concurrent watched runs in the same workspace, Magy acquires an exclusive lease per repository root. A second active watched run in the same repository is rejected until the active run completes, fails, or is cancelled.
6. **Continuation and Profile Pinning**: Supplying `continue_watch_id` to `watch_start` resumes a prior conversation. Magy pins the profile to the one used in the referenced run (bypassing round-robin) and passes `--conversation <conversation_id>` (captured from the initial run's `init` event, with `--continue` fallback). This guarantees deterministic conversation targeting and eliminates race conditions if other interactions occurred on that profile.
7. **Signal Publication & Pane Cleanup**: On process completion, an `.exit` signal file is atomically written. A detached monitor detects the signal, computes the final Git diff, records completion state, releases the repository lease, and closes the watched runner's floating execution pane. An orchestrating client can inspect diffs and test results with `watch_diff`.
8. **Cancellation**: Calling `watch_cancel` terminates the running process tree, closes the pane, releases the repository lease, preserves partial execution logs, and marks the status as `cancelled`.
