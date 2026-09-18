# Review Handoff

## Mission

Review one completed milestone for correctness, regressions, security risks, and
missing tests. Findings are primary output. Do not redesign later milestones
unless current code creates a blocking architectural problem.

## Inputs

- [`../plans/v1/README.md`](../plans/v1/README.md)
- Relevant milestone document.
- `docs/verification/mN-implementation.md`.
- Worktree diff and test output.

## Review Procedure

1. Map each implemented change to milestone step and exit criteria.
2. Inspect profile/auth boundaries before general code quality.
3. Trace process launch environment and executable resolution.
4. Trace every persistent write and lock boundary.
5. Trace failure cleanup, cancellation, and state finalization.
6. Check Linux, macOS, and Windows branches for semantic parity.
7. Inspect tests for accidental real-Agy or credential access.
8. Run focused tests and at least one full lint/test pass when feasible.

## Security Checklist

- No OAuth/token/account file content is read, copied, logged, or returned.
- Profile names and run IDs cannot traverse paths.
- Settings synchronization rejects symlink escape.
- Child-only home variables never mutate parent process state.
- Logs and MCP public state omit prompts, raw commands, and secret environment
  values unless explicitly requested by a private result operation.
- File permissions are restrictive where platform supports them.
- PID/process-group cancellation cannot kill unrelated processes.
- Default dangerous permission skip is visible in tool docs and README.
- No automatic task replay exists.

## Behavioral Checklist

- Round-robin cursor remains atomic under concurrent starts.
- No hard profile concurrency limit was introduced.
- Explicit profile selection behavior matches docs.
- Cooldown expiry and success reset work correctly.
- Agy stdout remains unpolluted by Magy diagnostics.
- MCP server writes protocol frames only to stdout.
- Detached run state survives server restart.
- Result chunks are stable and bounded.

## Output

Write `docs/verification/mN-review.md` with:

- Findings ordered by severity and file/line reference.
- Open questions and assumptions.
- Tests run and gaps.
- Exit-gate decision: pass, pass with follow-up, or fail.

If no findings exist, state that explicitly and list residual risks, especially
live-auth and untested OS behavior.
