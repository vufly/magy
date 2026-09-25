# Magy v1 Plan

## Outcome

Deliver two commands backed by one Python 3.11+ package:

- `magy`: launch `agy` with an explicit profile or automatic round-robin
  selection.
- `magy-mcp`: expose durable headless Agy runs over stdio MCP.

Python is chosen because MCP framing, typed tools, detached workers, locked
state, cancellation, and cross-platform process handling must be shared by both
commands. A launcher alone could be shell code, but splitting the product would
duplicate profile and routing behavior across shell, PowerShell, and MCP code.

## Decisions

- Use only the official local `agy` executable.
- Redirect Agy state with profile-specific home environment variables.
- Validate home-based account isolation before building profile routing.
- Sync only an explicit allowlist of non-auth settings.
- Permit concurrent runs on one profile; do not impose an artificial limit.
- Skip profiles with known auth, rate-limit, or quota failures.
- Never automatically replay a task that has started.
- Default MCP runs to `--dangerously-skip-permissions`, with an opt-out.
- Keep MCP v1 headless and durable. Interactive tmux control is out of scope.

## Constraints

- Agy 1.2.6 exposes no documented profile-root environment variable or CLI
  flag. Its documented state path is `~/.gemini/antigravity-cli`.
- `HOME` is the proposed switch on Linux/macOS. Windows also requires
  `USERPROFILE`, `HOMEDRIVE`, and `HOMEPATH`.
- Official documentation mentions native keyring authentication. Home-based
  state isolation must therefore be proven with two accounts before proceeding.
- Agy has no documented noninteractive quota-status command. A healthy profile
  means only that Magy has no current evidence of a limit failure.

## Milestones

| ID | Milestone | Exit gate |
| --- | --- | --- |
| M0 | [Foundation](milestone-0-foundation.md) | Package skeleton and fake Agy test harness work on all target OSes. |
| M1 | [Profile isolation](milestone-1-profile-isolation.md) | Two profile homes retain distinct authenticated accounts. |
| M2 | [Profiles, routing, and CLI](milestone-2-profiles-routing-cli.md) | `magy` manages and routes profiles without copying auth. |
| M3 | [Durable MCP](milestone-3-durable-mcp.md) | Detached runs survive MCP restart and support status/result/cancel. |
| M4 | [Hardening and release](milestone-4-hardening-release.md) | Cross-platform tests, review, verification, and user docs pass. |

Milestones are ordered. M1 is a hard compatibility gate. If M1 fails because
native keyring lookup ignores the synthetic home, stop. Do not continue with
credential swapping. Record findings and propose separate OS users, containers,
or proven platform-specific keyring isolation as a new plan.

## Target Package Shape

The implementation harness may refine module names, but responsibilities should
remain separated:

```text
pyproject.toml
src/magy/
  cli.py           # magy command and profile subcommands
  config.py        # platformdirs paths and user configuration
  profiles.py      # profile lifecycle and safe settings synchronization
  routing.py       # locked round-robin cursor and health state
  agy.py           # executable discovery, command building, health parsing
  runs.py          # durable run persistence and process control
  worker.py        # detached MCP worker entry point
  mcp_server.py    # stdio MCP tools
tests/
```

Prefer the smallest correct module set. Combine modules when separation adds no
clear ownership or test seam.

## Global Acceptance Criteria

1. Two managed profile homes retain distinct authenticated accounts.
2. Real `~/.gemini` authentication remains unchanged by managed-profile runs.
3. `magy` supports explicit and round-robin selection and skips known unhealthy
   profiles.
4. Concurrent runs may select the same profile; no one-run limit exists.
5. Limit failures affect only future selection. Magy never replays a started
   task automatically.
6. `magy-mcp` runs survive MCP server restart and support wait, status, result,
   and cancellation.
7. Automated tests pass on Linux, macOS, and Windows. Live authentication tests
   remain explicit and local.

## Non-Goals

- Proxying Google or Antigravity APIs.
- Reading, exporting, refreshing, or swapping OAuth credentials.
- Guaranteeing protection from provider enforcement.
- Interactive MCP terminal attachment, guarded input, or goal orchestration.
- Parsing private model reasoning from Agy trajectories.

## Extensions & Feature Plans

- [Human-in-the-Loop MCP Review Workflow](mcp-human-in-loop-terminal-ui.md): Specification for Zellij-backed reviewed runs, stream monitoring, and Git snapshot diff capture (delivered in M4).

---

## Verification & Architecture

- **Milestone Verification Reports:** Full audit evidence for each milestone is documented in [`verification/README.md`](verification/README.md).
- **Architecture Overview:** Implemented system architecture is documented in [`docs/architecture/overview.md`](../../architecture/overview.md).
