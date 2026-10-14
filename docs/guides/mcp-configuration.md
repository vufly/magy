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
| `watch_start` | Start a watched non-interactive Agy run in a user-facing terminal pane (Zellij floating pane or tmux split) with Git snapshots | `prompt` (required), `workspace`, `profile`, `model`, `agent`, `effort`, `mode`, `sandbox`, `additional_dirs`, `auto_approval` (default True, adds `--dangerously-skip-permissions`), `continue_watch_id`, `mux` (default `"auto"`, supports `"auto"`, `"zellij"`, `"tmux"`), `mux_cmd` |
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

The watched workflow (`watch_*`) allows an orchestrating agent to delegate tasks to Agy in a visible terminal pane (Zellij floating pane or tmux split), stream NDJSON output for real-time monitoring, preserve conversation context across follow-up iterations, and capture a clean Git patch diff excluding pre-existing repository dirt.

### Prerequisites

- An active Zellij session (`ZELLIJ` and `ZELLIJ_SESSION_NAME` present in environment) or tmux session (`TMUX` and `TMUX_PANE` present in environment), or a custom `mux_cmd`.
- `zellij` or `tmux`, and `git` binaries available on `PATH`.
- Target workspace must reside within an existing Git repository with a `HEAD` commit.

### Terminal Multiplexer Integration & Anchoring

`watch_start` natively supports Zellij and tmux, as well as custom multiplexers via `mux_cmd`:

1. **Multiplexer Selection (`mux`)**:
   - `mux="auto"` (default): Automatically detects available multiplexer session context. If both Zellij and tmux session contexts are present, **Zellij takes precedence**.
   - `mux="zellij"`: Explicitly chooses the Zellij backend. Requires `zellij` binary on `PATH` and active `ZELLIJ` + `ZELLIJ_SESSION_NAME` environment variables.
   - `mux="tmux"`: Explicitly chooses the tmux backend. Requires `tmux` binary on `PATH` and active `TMUX` + `TMUX_PANE` environment variables.
   - Explicit backends require a usable session and originating pane context; Magy does not create entire new sessions or silently fall back to headless execution.

2. **Native Zellij Anchoring & Focus**:
   - Launches `zellij run --floating --near-current-pane --no-focus --name <name> --cwd <workspace> -- <command>`.
   - Zellij uses the inherited `ZELLIJ_PANE_ID` environment variable to anchor the floating pane to the caller's origin tab, even if the user is currently viewing a different tab.
   - `--no-focus` prevents stealing focus or switching active tabs.
   - Use a Zellij version supporting these flags (verified on Zellij 0.45.1). Start the MCP server from the agent pane so it inherits `ZELLIJ_PANE_ID`.

3. **Native Tmux Splitting & Focus**:
   - Validates `tmux` binary, `TMUX`, and `TMUX_PANE`.
   - Extracts the exact socket path via `TMUX.rsplit(",", 2)[0]` and passes `-S <socket>` for all tmux invocations.
   - Creates a horizontal split pane with:
     ```bash
     tmux -S <socket> split-window -h -d -P -F "#{pane_id}" -t "$TMUX_PANE" -c <workspace> <quoted_command>
     ```
   - The runner command is safely shell-quoted via `shlex.join`.
   - `-d` ensures focus is preserved (never follows the focused window/pane).
   - Parses the native `%N` pane ID from output.
   - The backend name (`tmux`) and socket path are persisted in `WatchRunState` (with legacy states defaulting to `zellij` for backward compatibility).
   - Detached monitor liveness, cancellation, and cleanup route through the persisted backend and socket rather than current caller environment.
   - Accurately detects alive (`pane_dead=0`), dead (`pane_dead=1`), and missing panes.
   - `kill-pane` strictly targets only stable created `%N` pane IDs.

4. **Custom Multiplexers (`mux_cmd`) & Tmux Targeting**:
   - `mux_cmd` overrides native backend selection and invokes arbitrary multiplexers (e.g. WezTerm, i3, or custom scripts). Custom launchers do not expose native pane IDs; monitoring relies on the runner exit signal rather than launcher PID liveness.
   - `mux_cmd` receives the runner command appended or replaces `{cmd}` if present, receiving no Zellij or tmux flags.
   - **Important note on tmux targeting with `mux_cmd`**: `mux_cmd` executes argument vectors directly via `subprocess.Popen` and does **NOT** expand environment variables. Specifying commands such as `tmux split-window -d -t "$TMUX_PANE"` in `mux_cmd` will pass the literal string `"$TMUX_PANE"` to tmux. To target the originating pane using `mux_cmd`, wrap the command in a shell script/invocation (e.g., `["bash", "-c", "tmux split-window -d -t \"$TMUX_PANE\" \"$@\"", "--", "{cmd}"]`) or supply a resolved literal pane ID in argv (e.g., `["tmux", "split-window", "-d", "-t", "%12", "{cmd}"]`). Native `mux="tmux"` handles this targeting automatically.

### Execution Model

1. **Non-Interactive Execution Pane**: Unlike `pane_start` (which opens an interactive REPL in a split pane), watched runs launch `agy --print <prompt> --output-format stream-json` in a floating or split terminal pane. The user observes thinking and execution in real time as human-readable events rendered in the pane, while stderr is inherited live. The process exits automatically upon completion, leaving process control to the orchestrating agent.
2. **Tool Permissions and Auto-Approval**: In `stream-json` mode, Agy operates non-interactively and cannot prompt interactively for tool approvals. Like `run_start` and `pane_start`, `watch_start` defaults to `auto_approval=True` so that Agy passes `--dangerously-skip-permissions` to allow tool execution. Set `auto_approval=False` if unconfirmed tool actions should be denied.
3. **Mid-Run Monitoring**: Terminal events are recorded as raw NDJSON in `watches/<watch-id>/pty.log` while rendered in human-readable form in the pane. The harness agent can read chunked logs mid-run with `watch_log` to observe progress without blocking.
4. **Git Baseline Snapshots & Dirt Exclusion**: Prior to launching the pane, Magy creates a temporary Git index tree snapshot of tracked, staged, unstaged, and non-ignored untracked files without altering the user's working tree or index. When execution finishes, a final snapshot is taken and net diff computed:
   ```bash
   git diff --no-ext-diff --no-textconv --binary <baseline-tree> <final-tree>
   ```
   All pre-existing untracked files, staged changes, and unstaged modifications are excluded from the returned patch.
5. **Repository Concurrency Lease**: To prevent interleaving changes from concurrent watched runs in the same workspace, Magy acquires an exclusive lease per repository root. A second active watched run in the same repository is rejected until the active run completes, fails, or is cancelled.
6. **Continuation and Profile Pinning**: Supplying `continue_watch_id` to `watch_start` resumes a prior conversation. Magy pins the profile to the one used in the referenced run (bypassing round-robin) and passes `--conversation <conversation_id>` (captured from the initial run's `init` event, with `--continue` fallback). This guarantees deterministic conversation targeting and eliminates race conditions if other interactions occurred on that profile.
7. **Signal Publication & Pane Cleanup**: On process completion, an `.exit` signal file is atomically written. A detached monitor detects the signal, computes the final Git diff, records completion state, releases the repository lease, and closes the watched runner's execution pane. An orchestrating client can inspect diffs and test results with `watch_diff`.
8. **Cancellation**: Calling `watch_cancel` terminates the running process tree, closes the pane, releases the repository lease, preserves partial execution logs, and marks the status as `cancelled`.
