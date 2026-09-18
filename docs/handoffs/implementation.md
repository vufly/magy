# Implementation Handoff

## Mission

Implement Magy v1 milestone by milestone. Start at M0 and stop after each exit
gate for focused review. Do not begin M2 until M1 has a documented passing live
isolation result.

## Required Reading

1. [`../plans/v1/README.md`](../plans/v1/README.md)
2. Current milestone document under `docs/plans/v1/`
3. Any existing report under `docs/verification/`

## Working Rules

- Treat credential contents as out of bounds. Magy may launch Agy but must not
  parse, print, copy, or export OAuth tokens.
- Use official `agy`; do not implement proxy or API emulation.
- Keep profile environment changes local to child processes.
- Keep one shared implementation for CLI and MCP behavior.
- Permit concurrent use of a profile. Do not introduce a hard run limit.
- Never retry a started task automatically.
- Use fake Agy for automated tests. Real authentication is manual and opt-in.
- Preserve unrelated worktree changes.

## Milestone Loop

For each milestone:

1. Confirm prerequisites and previous exit gate.
2. Implement only current milestone scope.
3. Add or update tests named for milestone behavior.
4. Run focused tests, then full tests and lint.
5. Review diff for credential exposure and cross-platform assumptions.
6. Write `docs/verification/mN-implementation.md` with commands, results, known
   gaps, and exact files changed.
7. Hand off to review before starting next milestone.

## Required Implementation Report

Each report must include:

- Milestone and commit/worktree state.
- Completed step IDs, such as `M2-S3`.
- Design deviations and reasons.
- Commands run and pass/fail results.
- Manual actions still required.
- Security-sensitive paths touched, without contents.
- Known limitations and next milestone prerequisites.

## Stop Conditions

Stop and request direction when:

- M1 account identities are not isolated.
- A change would require reading or copying credential contents.
- Agy behavior differs materially from documented assumptions.
- Cross-platform behavior cannot be tested or safely emulated.
- Existing concurrent work directly conflicts with milestone files.
