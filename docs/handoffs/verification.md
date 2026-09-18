# Verification Handoff

## Mission

Independently verify milestone and release acceptance criteria using observable
behavior. Do not rely only on implementation reports or unit tests.

## Inputs

- [`../plans/v1/README.md`](../plans/v1/README.md)
- Relevant milestone document.
- Implementation and review reports under `docs/verification/`.
- Clean or known worktree state.

## Verification Layers

### Automated

Run documented lint and test commands. Confirm fake Agy, not real Agy, is used.
Check platform matrix results when verifying cross-platform criteria.

### Black-box CLI

From a clean temporary Magy data root, verify profile commands, explicit
selection, round-robin order, cooldown skipping, exit-code forwarding, and
stdout/stderr separation using fake Agy.

### Black-box MCP

Start the stdio server from a fresh process. Verify start, wait, status, result,
cancel, idempotency, server restart, and result chunking. Confirm stdout remains
valid MCP traffic.

### Live Agy

Run only with explicit user approval and local accounts. Never capture token
contents. Verify distinct profile identities, repeated launches, concurrent
launches, real-home preservation, and one minimal MCP run per profile.

## Evidence Rules

- Record commands with secrets and account identifiers redacted.
- Record versions, OS, exit status, and relevant bounded output.
- Record paths and file metadata, not credential contents.
- Separate observed evidence from inference.
- Mark skipped checks and explain why.

## Required Reports

For each milestone, write `docs/verification/mN-verification.md` containing:

- Environment and versions.
- Criteria-to-evidence table.
- Commands executed.
- Automated, black-box, and live results.
- Failures and reproduction steps.
- Final pass/fail decision.

For v1 release, write `docs/verification/v1-release.md` mapping evidence to all
global acceptance criteria from the plan index.

## Hard Failure Conditions

- Two managed homes resolve to the same effective account.
- Managed profile activity changes real-home authentication.
- Credential contents appear in logs, state, MCP output, or reports.
- A failed run is replayed automatically.
- MCP run disappears after server restart.
- Cancellation leaves the tested Agy process tree running.
- Cross-platform claims lack either native evidence or an explicit unverified
  qualification.
