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

Once configured, the following tools are available through MCP:

| Tool | Purpose | Key Parameters |
| --- | --- | --- |
| `magy_run_start` | Start a detached, asynchronous Agy run | `prompt` (required), `workspace`, `profile`, `timeout`, `auto_approval`, `idempotency_key` |
| `magy_run_headful` | Open interactive Agy in a split pane in active Zellij session; returns pane metadata immediately | `prompt` (required), `workspace`, `profile`, `model`, `agent`, `effort`, `mode`, `sandbox`, `additional_dirs`, `auto_approval` |
| `magy_run_wait` | Short-poll until a run completes or times out | `run_id`, `timeout` (default 20s, max 60s) |
| `magy_run_status` | Query safe run status | `run_id` |
| `magy_run_result` | Retrieve bounded output chunks with stable offsets | `run_id`, `offset` (default 0), `limit` (default 64 KiB) |
| `magy_run_cancel` | Terminate a running or queued process tree | `run_id` |
| `magy_profiles` | Query registered profiles, availability, and routing | None |
| `magy_run_review_start` | Start a reviewed non-interactive Agy run in a floating Zellij pane with Git snapshots | `prompt` (required), `workspace`, `profile`, `model`, `agent`, `effort`, `mode`, `sandbox`, `additional_dirs`, `auto_approval` (default False), `continue_review_id` |
| `magy_run_review_status` | Query review run status (`running`, `completed`, `failed`, `cancelled`) | `review_id` (required) |
| `magy_run_review_wait` | Short-poll until a review run completes, fails, or is cancelled | `review_id` (required), `timeout` (default 20s, max 60s) |
| `magy_run_review_log` | Retrieve bounded PTY terminal log chunks for mid-run monitoring | `review_id` (required), `offset` (default 0), `limit` (default 64 KiB) |
| `magy_run_review_cancel` | Cancel a review run, terminate processes, close pane, and release repo lease | `review_id` (required) |
| `magy_run_review_result` | Retrieve bounded chunks of the net Git diff produced by the run | `review_id` (required), `offset` (default 0), `limit` (default 64 KiB) |

> [!NOTE]
> `magy_run_start` defaults to `auto_approval=True` (`--dangerously-skip-permissions`)
> to enable unattended headless delegation. Set `auto_approval=False` if tool
> actions require interactive approval. See [`docs/architecture/security.md`](../architecture/security.md) for details on trust boundaries.

`magy_run_headful` requires OpenCode and its Magy MCP server to run inside an
active Zellij session. It opens Agy in a right split and returns the pane ID;
live interaction and output remain in that pane, with no MCP wait/result
capture. It also defaults to `auto_approval=True` and accepts
`auto_approval=False` for interactive approvals in the pane. If no active
Zellij session is available, the tool fails instead of switching to headless
execution.

---

## 4. Human-in-the-Loop Review Workflow (Terminal UI & Git Snapshots)

The review workflow (`magy_run_review_*`) allows an orchestrating agent to delegate tasks to Agy in a visible floating Zellij pane, stream PTY output for real-time monitoring, preserve conversation context across follow-up iterations, and capture a clean Git patch diff excluding pre-existing repository dirt.

### Prerequisites

- An active Zellij session (`ZELLIJ` and `ZELLIJ_SESSION_NAME` present in environment).
- `zellij` and `git` binaries available on `PATH`.
- Target workspace must reside within an existing Git repository with a `HEAD` commit.

### Execution Model

1. **Non-Interactive Floating Pane**: Unlike `magy_run_headful` (which opens an interactive REPL in a split pane), reviewed runs launch `agy --print <prompt>` in a floating Zellij pane. The user observes thinking and execution in real time. The process exits automatically upon completion, leaving process control to the orchestrating agent.
2. **Interactive Tool Approvals**: `magy_run_review_start` defaults to `auto_approval=False` so that Agy tool-approval prompts remain visible for user confirmation in the pane. Setting `auto_approval=True` passes `--dangerously-skip-permissions` if non-interactive tool execution is desired.
3. **Mid-Run Monitoring**: Terminal output is captured via a PTY transcript in `reviews/<review-id>/pty.log`. The harness agent can read chunked logs mid-run with `magy_run_review_log` to observe progress without blocking.
4. **Git Baseline Snapshots & Dirt Exclusion**: Prior to launching Zellij, Magy creates a temporary Git index tree snapshot of tracked, staged, unstaged, and non-ignored untracked files without altering the user's working tree or index. When execution finishes, a final snapshot is taken and net diff computed:
   ```bash
   git diff --no-ext-diff --no-textconv --binary <baseline-tree> <final-tree>
   ```
   All pre-existing untracked files, staged changes, and unstaged modifications are excluded from the returned patch.
5. **Repository Concurrency Lease**: To prevent interleaving changes from concurrent review runs in the same workspace, Magy acquires an exclusive lease per repository root. A second active reviewed run in the same repository is rejected until the active run completes, fails, or is cancelled.
6. **Continuation and Profile Pinning**: Supplying `continue_review_id` to `magy_run_review_start` resumes a prior conversation. Magy pins the profile to the one used in the referenced review run (bypassing round-robin) and appends `--continue` to Agy's arguments. This ensures Agy accesses the prior run's conversation SQLite store inside the profile's synthetic home.
7. **Signal Publication & Pane Cleanup**: On process completion, an `.exit` signal file is atomically written. A detached monitor detects the signal, computes the final Git diff, records completion state, releases the repository lease, and closes the floating pane via `zellij action close-pane`.
8. **Cancellation**: Calling `magy_run_review_cancel` terminates the running process tree, closes the Zellij pane, releases the repository lease, preserves partial PTY logs, and marks the status as `cancelled`.
