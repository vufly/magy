# Magy v1 Retrospective & Summary

This document provides a concise historical record of the Magy v1 release: milestone outcomes, core architectural decisions, empirical verification evidence (including the Milestone 1 isolation gate and its limitations), and unresolved backlog items.

---

## 1. Outcome & Delivery

Magy v1 delivers a multi-profile orchestrator, resilient runner, and Model Context Protocol (MCP) server for the Google Antigravity CLI (`agy`), packaged as a single Python 3.11+ distribution providing two console entry points:

- `magy`: CLI for profile management, health inspection, and profile-isolated `agy` execution with automatic round-robin rotation.
- `magy-mcp`: Stdio MCP server exposing detached asynchronous runs, interactive headful Zellij sessions, and human-in-the-loop review runs with Git snapshot diffing.

---

## 2. Milestone History

| Milestone | Scope | Outcome |
| :--- | :--- | :--- |
| **M0: Foundation** | Project skeleton, storage helpers, path discovery, fake Agy test harness. | Established `src/` layout, `platformdirs` storage, private atomic file replacement (`0o700`/`0o600`), strict identifier validation (`fullmatch`), and non-executing fake Agy harness. |
| **M1: Profile Isolation Gate** | Empirical validation of synthetic home account separation. | **PASS**. Proved two distinct Google accounts remain isolated across repeated and concurrent launches using process-level `$HOME` redirection on official Agy 1.2.6. |
| **M2: Profiles, Routing, & CLI** | Profile registry, settings synchronization, round-robin cursor, health parsing, CLI. | Implemented profile registry with lifecycle leases and UUID incarnation IDs, allowlist settings sync with TOCTOU symlink protection, locked round-robin routing, multi-unit cooldown parsing, direct stream inheritance, and `agy_resolver` protocol. |
| **M3: Durable MCP Server** | Stdio MCP server, detached worker supervisor, cross-process cancellation, result pagination. | Implemented `magy-mcp` with 6 core tools, detached execution (`setsid` / `CREATE_NEW_PROCESS_GROUP`) surviving server restarts, atomic idempotency, anti-PID-recycling safeguards, and bounded UTF-8 chunked pagination. |
| **M4: Hardening & Release** | Review runner, compatibility diagnosis, security review, documentation, packaging. | Added Zellij-backed human-in-the-loop review runner with Git baseline snapshots and conversation continuation, `magy doctor` compatibility reporting, comprehensive security and architecture docs, and release packaging (`wheel`/`sdist`). |

---

## 3. Important Architectural Decisions

### 3.1 Synthetic Home & Isolation Boundary
- **Home Redirection**: Profile isolation is achieved by setting `HOME` (and on Windows: `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`) to `<data_dir>/profiles/<name>/home/`.
- **POSIX XDG Derivation**: Profiles derive local `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME` inside the profile home to prevent external shims from falling back to real user directories.
- **Environment Protection**: Isolation variables are strictly guarded against caller overrides; parent process environment remains unmutated.

### 3.2 Safe Settings Synchronization
- **Strict Allowlist**: Only safe configuration files (`GEMINI.md`, `antigravity.json`, `tools.json`, `mcp_config.json`, `extensions.json`, `keybindings.json`, `settings.json`) and directories (`rules/`, `skills/`, `extensions/`, `workflows/`) are copied from host `~/.gemini/`.
- **Credential Exclusion**: All credential files, OAuth tokens, installation IDs, histories, conversations, and SQLite databases are strictly excluded.
- **TOCTOU & Symlink Defenses**: Traversal verifies directory descriptors (`dir_fd`) and `O_NOFOLLOW` on supported platforms; source symlinks escaping the root and any destination symlinks cause immediate abort.

### 3.3 Concurrency, Leases, and Incarnation Tracking
- **Lifecycle Leases**: Shared leases (`fcntl.LOCK_SH` on POSIX, `kernel32.LockFileEx` on Windows) protect running profiles during execution; exclusive leases prevent mutating or removing active profiles.
- **Incarnation Verification**: Each profile creation assigns an immutable UUID incarnation ID, verified under lock before launch to eliminate ABA removal/recreation races.
- **Concurrent Execution**: Magy imposes no artificial concurrency limit per profile; multiple runs may execute against the same profile simultaneously.

### 3.4 Discovery & Tool-Manager Resolvers
- **Direct Candidate Resolution**: Discovery scans `PATH` and skips multicall shims (e.g. `mise`, `asdf`), selecting direct executables.
- **Dynamic Resolver Protocol**: Configured `agy_resolver` (e.g. `["mise", "which", "agy"]`) executes on every probe and launch without caching, allowing upstream tool updates to take effect immediately.

### 3.5 Routing, Health, & No-Replay Guarantee
- **Locked Round-Robin**: A persistent cursor stored under a short file lock distributes tasks across available profiles.
- **Health Backoff**: Profiles encountering rate limits or quota exhaustion enter cooldown (parsed from provider retry hints or default 60s); auth failures mark profiles unhealthy until re-authenticated.
- **No-Replay Guarantee**: Prompts failing mid-execution are never silently resubmitted to another profile, preventing duplicate side effects (e.g. commits, API mutations).

### 3.6 Durable Workers & Safe Process Lifecycle
- **Detached Execution**: Worker processes are spawned in new sessions (`start_new_session=True` on POSIX, `CREATE_NEW_PROCESS_GROUP` on Windows) with state persisted before spawn.
- **Anti-PID-Recycling Safeguards**: Process identity is validated using process creation timestamps and per-run UUID marker files (`/tmp/magy-worker-<pid>.marker` and `MAGY_RUN_ID` environment tags) before signaling.
- **Descendant Reaping**: Cancellation and timeout reap the entire descendant process tree via PGID signaling and `/proc` process traversal.

### 3.7 Human-in-the-Loop Review Runner
- **Non-Interactive Floating Pane**: Runs `agy --print <prompt> --output-format stream-json` in a floating Zellij pane. Real-time events are rendered in the pane while raw events are logged to `pty.log`. The pane closes automatically upon completion.
- **Rationale**: Avoids fragile terminal scraping or keystroke injection (`zellij write-chars`), provides clean cancellation via process tree termination, and allows programmatic follow-up via `continue_review_id`.
- **Git Baseline Dirt Exclusion**: Captures a temporary Git index tree snapshot of tracked, staged, unstaged, and untracked files prior to execution, computing a clean net diff:
  ```bash
  git diff --no-ext-diff --no-textconv --binary <baseline-tree> <final-tree>
  ```
- **Conversation Continuation**: Resuming via `continue_review_id` pins the profile and passes `--conversation <id>` (with `--continue` fallback), preserving SQLite conversation context across iterations.
- **Repository Concurrency Lease**: Exclusive lease per repository root prevents interleaved edits from concurrent review runs.

---

## 4. Verification Evidence & Limitations

### 4.1 Milestone 1 Isolation Gate (Empirical Evidence)
The M1 gate empirically validated synthetic home isolation using official Antigravity CLI 1.2.6 on Linux (kernel 6.18, WSL2, x86_64):

- **Distinct Account Authentication**: Two separate Google accounts (`account-A`, `account-B`) were authenticated into dedicated profile homes. OAuth credentials were saved exclusively under `<data_dir>/profiles/<name>/home/.gemini/antigravity-cli/antigravity-oauth-token` (mode `0o600`).
- **Repeated Invocations**: Both profiles retained authentication across repeated inference and model listing commands without prompting.
- **Concurrent Parallel Invocations**: Both profiles were launched simultaneously as background processes (`CONC-A` and `CONC-B`), completing with exit code 0 without database collisions or identity crosstalk.
- **Logout Isolation**: Deleting credentials for Profile A caused Profile A to report unauthenticated status, while Profile B remained authenticated and fully operational.
- **Zero Real-Home Tampering**: A metadata snapshot tool (`src/magy/testing/snapshot.py`) compared ~2,300 entries in the real user `~/.gemini/` directory before and after the probe, confirming 0 credential modifications or leaks.
- **Keyring Assessment**: No shared-keyring prompts or identity leaks were observed on Linux/WSL2.

#### Known Limitations
- The empirical isolation gate confirms separation on the tested Agy version (1.2.6) and Linux environment.
- If a future upstream release of Antigravity switches credential storage to an unscoped, system-wide OS keyring (e.g. macOS Keychain or Windows Credential Manager) without per-home namespacing, synthetic `$HOME` redirection alone may not separate login states. `magy doctor` detects and warns about unverified Agy versions.

### 4.2 Automated Test Suite
- **M4 Linux Verification**: 285 tests passed on each of Python 3.11 and Python 3.14 at the M4 release gate.
- **M4 Code Quality**: Ruff linter and formatter passed at the M4 release gate.
- **Packaging Smoke Test**: Clean wheel (`dist/magy-0.1.0-py3-none-any.whl`) and source distribution built and tested in an isolated virtual environment with `uv tool install`.

### 4.3 Scoping & Avoidance of Unverified Claims
- **CI Matrix**: Automated CI was validated on Linux; native macOS and Windows CI runs remain deferred in the active backlog.
- **Live Authentication**: Live OAuth authentication was validated locally on Linux/WSL2; no live credentials or tokens were used in automated CI or tested on native Windows/macOS.

---

## 5. Unresolved Issues & Deferred Work

Actionable engineering follow-ups are tracked in [`docs/plans/backlog.md`](../backlog.md):

1. **Cross-Platform CI Verification**: Execute the full test matrix on native macOS and Windows GitHub Actions runners for Python 3.11 and 3.14.
2. **Native Windows Verification**: Validate native process group creation, `kernel32` lifecycle locking, file ACL protections, and junction/reparse-point traversal behavior.
3. **Native macOS Verification**: Validate process-group signaling and detached descendant discovery without `/proc` markers.
