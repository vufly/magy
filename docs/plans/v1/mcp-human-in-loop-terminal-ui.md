# Plan: Human-in-the-Loop MCP Runs with Zellij and Git Snapshots

## Goal

Add an MCP workflow that runs Agy non-interactively in a user-facing floating
Zellij pane so the user can watch thinking and execution in real time, cancel
if unsatisfied, and continue with a follow-up prompt that preserves conversation
context. The workflow returns a durable Git diff after each run. The harness
agent drives the loop; no interactive shell fallback is needed.

This is a planning document only. Implementation begins after explicit approval.

## Execution model (revised)

Use `agy --print <prompt>` (non-interactive) in a floating Zellij pane instead
of `--prompt-interactive`. The pane is fully visible — the user sees all Agy
thinking and tool execution — but the process exits automatically on completion
rather than waiting for further input. The harness agent controls the loop via
MCP tools.

**Why non-interactive over interactive:**

- Clean process lifecycle: cancel = kill process, no pane state to manage.
- Harness drives follow-up via `continue_review_id`; no need to inject into a
  live REPL via fragile `zellij write-chars`.
- Conversation context is preserved across calls through Agy's `--continue` flag
  combined with profile pinning (see Module 1 §2).

The fallback interactive shell is removed from scope. The user may always open a
separate terminal for manual intervention.

## Current flow and contract

- `magy_run_start` starts a durable detached Agy run. Its worker is launched
  through Python `subprocess.Popen` in `src/magy/runs.py` and executed by
  `src/magy/worker.py`.
- `magy_run_headful` opens an interactive Agy session in a right-side Zellij
  split and returns pane metadata immediately. Its launch path is in
  `src/magy/headful.py`.
- Other MCP tools wait for, inspect, retrieve output from, or cancel detached
  runs. No `agy_coder` tool or Node.js `child_process.exec` flow exists today.

Keep both existing execution contracts. Add a separate reviewed workflow. Its
start call returns a review ID and pane ID; clients use separate status, wait,
log, and result calls to retrieve output and the final diff. This avoids keeping
one MCP request open throughout the run and protects the workflow from MCP
client timeouts.

## Module 1 — Zellij execution

1. Add a reviewed-run launcher and private per-run artifacts under Magy's state
   directory: `reviews/<review-id>/` containing `state.json` (profile, workspace,
   baseline, status, exit codes), PTY log, and completion signal.

2. **Profile pinning and continuation.** Select the Agy profile once at start
   and persist it in `state.json`. When `continue_review_id` is supplied, load
   the prior review's `state.json`, extract its profile, and pass that profile
   explicitly to `select_profile(explicit_name=...)` — bypassing round-robin.
   Then add `--continue` to the Agy argument list. This ensures follow-up calls
   reopen the correct profile's conversation store.

   Each profile has an isolated `HOME` under
   `~/.local/share/magy/profiles/<name>/home/`. Agy's conversation store lives
   at `.gemini/antigravity-cli/conversations/<id>.db` inside that home. Because
   `--continue` loads from the calling profile's store, pinning the profile is
   the only reliable way to resume the same conversation context across separate
   MCP calls.

3. Resolve and validate the workspace and Git root. Require an existing Git
   repository with a `HEAD`, an active Zellij session, and Git and Zellij
   executables. Reuse Magy's profile-aware CLI
   (`magy.cli --profile <selected> -- --print <prompt> [--continue] ...`) so
   profile credential isolation, settings synchronisation, and health handling
   remain consistent. Preserve supported Agy options (`--model`, `--agent`,
   `--effort`, `--mode`, `--sandbox`, `--add-dir`). Default reviewed runs to
   `auto_approval=False` so Agy tool-approval prompts remain visible.

4. Generate an owner-only Bash script that runs Agy, captures its exit code, and
   writes the `.exit` signal on completion. Launch it through an argument-vector
   command equivalent to:

   ```text
   zellij run --floating --name magy-task-<review-id> --cwd <workspace> -- bash -c 'exec bash "$1"' magy-review <script-path>
   ```

   Pass prompt and options as safely encoded data/arguments; do not interpolate
   user-controlled text into shell source. Capture the Zellij pane ID from its
   output.

5. Record Agy's PTY output in a per-run terminal log using a PTY transcript
   (not by redirecting output away from the TTY). Preserve Agy's exit status.
   The pane exits automatically when Agy finishes. Treat launch errors and a
   pane closed before signal publication as failed/interrupted runs.

6. Atomically publish a run-specific `.exit` signal containing Agy's exit code
   when the script completes. No fallback shell is started.

## Module 2 — Git snapshot state management

1. Before starting Zellij, capture `git rev-parse HEAD` and snapshot the
   repository's tracked, staged, unstaged, and non-ignored untracked content
   into a temporary Git index/tree. Store baseline commit/tree IDs and repository
   root in `state.json`. Do not modify the user's index or worktree.
2. Exclude all pre-existing workspace changes from the returned diff. To avoid
   confusing concurrent changes from separate review runs in the same
   repository, reject a second active reviewed run for that repository; release
   the repository lease on completion or failure.
3. A detached monitor polls for the run's unique `.exit` signal without
   blocking the MCP server. Once detected, snapshot final workspace state using
   the same temporary-index method and produce one net patch between baseline
   and final trees, for example:

   ```text
   git diff --no-ext-diff --no-textconv --binary <baseline-tree> <final-tree>
   ```

   This includes any commits the user makes manually in a separate terminal and
   final uncommitted edits, while excluding pre-existing staged/unstaged/untracked
   changes. Include new non-ignored files, deletions, and renames; ignored files
   remain outside the snapshot. Scope the diff to the validated repository root.
   Changes made by unrelated processes during the review cannot be distinguished
   from review changes and should be documented as such.
4. Persist final patch, completion status, Agy exit code, and safe error
   metadata. Bound result retrieval by byte offsets, consistent with existing
   `RunResult` conventions, to support large diffs. Record errors rather than
   leaving runs indefinitely pending.
5. After processing completion, close only the recorded pane ID with a targeted
   Zellij command, such as `zellij action close-pane --pane-id <pane-id>`, when
   possible. Pane cleanup failure must not discard a saved result.
6. Test with temporary Git repositories. Cover exclusion of pre-existing dirty,
   staged, and untracked changes; Agy edits plus user commits plus final
   uncommitted edits; new files, deletions, empty diffs; multiple repositories
   and concurrent-run exclusion; early pane closure; missing Git or `HEAD`;
   signal publication; monitor restart; and targeted pane cleanup.

## Module 3 — MCP tool schema and documentation

1. Register a durable tool set in `src/magy/mcp_server.py`:
   - `magy_run_review_start`: start the reviewed run and return review ID, pane
     ID, profile used, and workspace. Accepts optional `continue_review_id` to
     resume a prior conversation on the same profile.
   - `magy_run_review_status`: report `running/completed/failed/cancelled` state.
   - `magy_run_review_wait`: bounded, short-poll wait for state changes.
   - `magy_run_review_log`: return PTY log chunks by byte offset for mid-run
     monitoring. Allows the harness agent to observe Agy's output without
     polling status alone.
   - `magy_run_review_cancel`: kill the running Agy process and pane, mark run
     cancelled, preserve any partial PTY log.
   - `magy_run_review_result`: return final diff in bounded chunks, including
     byte offsets, EOF, and Agy exit code.

2. Make the review contract explicit in the tool schema:

   > This tool executes Agy non-interactively in a user-facing Zellij pane. The
   > user can watch thinking and execution in real time. Supply
   > `continue_review_id` to resume a prior run's conversation on the same
   > profile. Cancel via `magy_run_review_cancel` if intervention is needed,
   > then call `magy_run_review_start` again with the prior review ID and a
   > refined prompt. The start call returns immediately; the final Git diff is
   > retrieved with `magy_run_review_result` after the run completes.

   Keep existing strict input validation and sanitized error behaviour.

3. Update `README.md` and `docs/mcp-configuration.md` to describe Zellij
   prerequisites, non-interactive headful behaviour, `continue_review_id`
   semantics, profile pinning, baseline exclusion, log location,
   exit/result semantics, and how the new workflow relates to existing detached
   and headful tools.

4. Add focused MCP schema, start/status/log/cancel/result, transport, and
   sanitized-error tests. Verify terminal logging does not leak output onto the
   MCP stdio transport.

## Verification

Run checks in this order after implementation:

```bash
uv run pytest tests/<focused-review-tests>.py
uv run pytest
uv run ruff check .
uv run ruff format --check .
git diff --check
```

Implement modules step-by-step after approval.
