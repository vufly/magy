# Milestone 1 Review: Profile Isolation Gate

## Decision

**Pass.** All Milestone 1 findings (H1, H2, H3, M1, M2, M3, M4) are resolved.
The two-account live isolation probe has been completed successfully with official
Agy 1.2.6. Milestone 1 is approved; Milestone 2 is unblocked.

## Findings Status

| Finding | Severity | Status | Evidence |
| --- | --- | --- | --- |
| H1: Unapproved M0 foundation | High | RESOLVED | M0 R1/R2 remediated, passed, and committed as `2aef7fb`. |
| H2: Incomplete live isolation gate | High | RESOLVED | Live 2-account probe completed; repeated, concurrent, and logout isolation verified with Agy 1.2.6 in `docs/verification/m1-verification.md`. |
| H3: Symlink redirect vulnerability | High | RESOLVED | `_check_no_symlink_and_contained` rejects symlinks and escapes. Tests pass in `tests/test_profiles.py`. |
| M1: Configured binary ignored | Medium | RESOLVED | `run_in_profile` resolves `config.json` `agy_cmd` with full M0 precedence and bounded errors. |
| M2: Protected variable overrides | Medium | RESOLVED | `PROTECTED_ENV_VARS` enforced; caller overrides rejected. Parent `os.environ` unmutated. |
| M3: Premature passthrough CLI | Medium | RESOLVED | Top-level `--profile` passthrough removed. Clean M1 subcommands `create`, `auth`, `run` preserve `--` child args. |
| M4: Windows path semantics | Medium | RESOLVED | `apply_home_to_env` uses `ntpath.splitdrive`; drive-letter and UNC regressions pass on Linux. |

## Test Gaps

- Concurrent profile test counts fast fake invocations but does not prove
  process overlap or persistent identity separation.
- No tests verify child nonzero exit-code forwarding, inherited CWD, standard
  stream passthrough, parent-environment immutability, or bounded CLI errors for
  missing executables/storage failures.
- No fake profile state persists across launches, so automated tests do not
  model authentication reuse.

## Positive Review Notes

- Profile names use strict single-component validation.
- Default environment construction copies rather than mutates `os.environ`.
- XDG variables remain untouched.
- Default subprocess execution inherits CWD and terminal streams and uses no
  shell interpolation.
- Production profile code does not read, parse, copy, or log token contents.
- Fake Agy records an environment allowlist by default.

## Commands Run

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Linux/Python 3.14.7. |
| `uv run pytest` | PASS | 76 tests on Linux/Python 3.14.7. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python. |
| `uv run --python 3.11 pytest` | PASS | 76 tests on Linux/Python 3.11.16. |
| `uv build` | PASS | Built wheel and source distribution. |
| Profile-home symlink reproduction | FAIL as expected | External target received `.gemini`. |
| Protected environment override reproduction | FAIL as expected | Child `HOME` became `/real`. |
| Nested `--profile` reproduction | FAIL as expected | Selected B instead of A. |
| Windows `splitdrive` reproduction | FAIL as expected | Linux host returned empty drive. |

No real Agy command or credential file was accessed during automated review
verification.

## Remediation Handoff

1. Stop M1 work and close M0 R1/R2 with final M0 approval.
2. Reject profile path redirects and protect child isolation variables.
3. Restore configured executable precedence for managed launches.
4. Remove premature M2 passthrough or make temporary M1 CLI unambiguous.
5. Add focused launcher, symlink, failure-path, and persistent fake-state tests.
6. Run Ruff, pytest on Python 3.11/default Python, and package build.
7. Complete the explicit user-driven two-account live probe and compatibility
   report on Linux without recording credential content.
8. Request M1 re-review. Do not begin M2 before M1 receives a pass decision.

Native macOS and Windows execution remains deferred in `docs/backlog.md` and is
not the reason for this M1 failure.

## Recommended Verification Process

Run verification in stages. Do not request real OAuth interaction until all
automated safety gates pass.

### Stage 1: Automated safety gate

Verify before launching official Agy with a managed home:

- Profile root, `home`, and `.gemini` are private and are not symlinks.
- Every resolved profile path remains below the managed profiles root.
- Protected home/isolation variables cannot be replaced by caller overrides.
- Executable discovery follows `MAGY_AGY_CMD`, configured `agy_cmd`, then
  `PATH`, before profile home variables are changed.
- Building and launching a child environment does not mutate parent
  `os.environ`.
- A metadata-only snapshot of real `~/.gemini` can be compared before and after
  live probes without reading file contents.

This stage must pass Ruff, pytest on Python 3.11 and the default Python, and
package build before Stage 3 begins.

### Stage 2: Persistent fake-account verification

Extend Fake Agy with harmless state stored beneath its received `HOME`. Do not
model or name the state as an OAuth token.

The fake harness should support:

- Assigning a profile-local account alias marker.
- Reading that marker on later launches.
- Removing only the current profile's marker to simulate logout.
- Sleeping after recording the marker so concurrent overlap can be proven.

Automated tests should prove:

1. Profile A and Profile B store different aliases under different homes.
2. Repeated launches reuse each profile's alias.
3. Two sleeping launches overlap and retain their intended aliases.
4. Logging out Profile A does not alter Profile B.
5. Real-home metadata and parent environment remain unchanged.

### Stage 3: Guided live verification

This stage requires user interaction because OAuth account selection must remain
visible and private. Use official Agy only after Stages 1 and 2 pass.

1. Record Agy version, OS, and a metadata-only snapshot of real `~/.gemini`.
2. Run `magy profile auth profile-a`; user selects the first test account.
3. Run `magy profile auth profile-b`; user selects a distinct test account.
4. Use local redacted aliases such as `account-A` and `account-B`; do not record
   email addresses, OAuth URLs, screenshots with identity data, or tokens.
5. Run `models` and one minimal print request through each profile twice.
6. Run both profiles concurrently and confirm each retains the expected alias.
7. With user approval, log out or invalidate Profile A only; verify Profile B
   remains authenticated and usable.
8. Compare real-home metadata after the probe and record whether it changed.

Record "real-home authentication not modified" when supported by metadata.
Do not claim files were not read unless separate file-access tracing proves it.

### Optional Linux access tracing

When available, trace filesystem open/access operations for the managed Agy
child and check for access to real `~/.gemini`. Keep raw traces local and
ephemeral because they may contain sensitive paths. Record only summarized
pass/fail evidence in repository documents.

### Stage 4: Compatibility report

Create or update `docs/verification/m1-verification.md` with:

- Agy version and OS, scoped accurately to the tested platform.
- Sanitized commands and exit statuses.
- Redacted aliases and expected/observed identity separation.
- Managed filesystem paths created, without file contents.
- Repeated, concurrent, and one-profile-logout results.
- Real-home metadata comparison.
- Keyring prompts or unexpected cross-profile behavior.
- Explicit pass/fail decision.

## User Interaction Required

The user is needed only for private account actions:

- Complete two browser OAuth sign-ins with distinct accounts.
- Confirm expected identities using redacted aliases only.
- Confirm repeated and concurrent launches retain those aliases.
- Approve logging out or invalidating one test profile.
- Confirm the other profile remains authenticated afterward.

The user must not provide passwords, email addresses, tokens, OAuth URLs, or
credential file contents. All setup, metadata capture, commands, concurrency,
cleanup, and report generation should otherwise be automated.
