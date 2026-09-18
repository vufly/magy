# Milestone 1 Review: Profile Isolation Gate

## Decision

**Pass with follow-up.** User directly confirmed that two managed profiles were
authenticated with distinct accounts and operated concurrently without identity
crosstalk on Agy 1.2.6 under Linux/WSL2. This satisfies M1's central
compatibility gate: profile-specific home variables are sufficient for separate
authenticated instances on the tested platform. M2 is unblocked.

Remaining items are implementation hardening and automated-test precision. They
do not invalidate the observed two-account result and do not require another
OAuth probe unless launch environment behavior changes.

## Gate Evidence

- M0 is approved and committed as `2aef7fb`.
- User selected two distinct accounts for `profile-a` and `profile-b`.
- Both profiles retained authentication across repeated launches.
- Both profiles produced successful responses while running in parallel.
- Invalidating Profile A left Profile B authenticated and usable.
- Real-home authentication metadata showed no authentication-file modification.
- No credential contents, account emails, passwords, or OAuth URLs were added to
  repository artifacts.

## Resolved Findings

| Finding | Status | Evidence |
| --- | --- | --- |
| Unapproved M0 foundation | RESOLVED | M0 approved at `2aef7fb`. |
| Configured executable ignored | RESOLVED | Configured `agy_cmd` precedence tests pass. |
| Protected environment overrides | RESOLVED | Seven protected override keys are rejected. |
| Premature top-level `--profile` parsing | RESOLVED | Top-level M2 passthrough removed. |
| Windows path splitting | RESOLVED | `ntpath` drive-letter and UNC tests pass. |
| Direct profile/home/.gemini symlinks | RESOLVED | Direct links are rejected before launch. |
| Two-account live compatibility | RESOLVED | User-confirmed repeated and parallel distinct-account operation. |

## Required Follow-ups

### F1: Complete profile-state symlink hardening

- **Priority:** High
- **Target:** First M2 hardening change; required before release
- **Location:** `src/magy/profiles.py:25-87`

A pre-existing `<data>/profiles` symlink is followed and its external target is
treated as the managed root. A pre-existing
`.gemini/antigravity-cli` symlink is also accepted. These require a locally
modified or pre-tampered profile tree and were not present during the successful
live probe, but they can redirect future authentication state outside managed
storage.

Follow-up:

- Reject a symlinked profiles root before resolving or creating it.
- Reject links through the known `.gemini/antigravity-cli` credential subtree.
- Add external-target, real-home, and cross-profile regressions.
- Track native Windows junction/reparse behavior with cross-platform backlog.

### F2: Make fake concurrency proof deterministic

- **Priority:** Medium
- **Target:** Early M2 test hardening
- **Location:** `tests/test_profiles.py:225-245`

The current `duration >= 0.2` assertion also passes for sequential execution.
Use ready markers/barriers or a safe upper duration below sequential runtime, and
assert each worker's expected alias. The user-confirmed live parallel result
still satisfies M1's compatibility gate.

### F3: Finalize strict CLI passthrough parsing

- **Priority:** Low
- **Target:** M2-S6
- **Location:** `src/magy/cli.py:124-165`

Unknown top-level options can be collected by `parse_known_args()` and appended
to Agy arguments. Implement strict Magy parsing when M2 adds final passthrough
syntax, with remainder handling only after the selected profile command or
explicit `--` delimiter.

### F4: Integrate metadata snapshot helper cleanly

- **Priority:** Immediate documentation artifact cleanup
- **Target:** Before next commit
- **Location:** `src/magy/testing/snapshot.py`

The newly added helper is currently untracked and Ruff reports an unused `json`
import plus an overlong docstring. Fix and test the helper before committing the
documentation that references it, or remove the helper reference if it is not
intended to ship.

## Evidence Scope

- Account identity is based on the user's private OAuth account selection and
  confirmation. Agy did not expose a safe programmatic identity command.
- Metadata comparison supports "real-home authentication not modified," not a
  categorical claim that official Agy never opened any real-home path.
- Results show effective account separation and no observed shared-keyring
  crosstalk on Agy 1.2.6 under Linux/WSL2. They do not prove internal keyring
  implementation details.
- Native macOS and Windows execution remains deferred in `docs/backlog.md`.

## Commands Run

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Linux/Python 3.14.7. |
| `uv run pytest` | PASS | 104 tests on Linux/Python 3.14.7. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python. |
| `uv run --python 3.11 pytest` | PASS | 104 tests on Linux/Python 3.11.16. |
| `uv build` | PASS | Built wheel and source distribution. |
| Current-worktree `uv run ruff check .` | FAIL | New untracked `snapshot.py` has F401 and E501. |

Reviewer did not rerun OAuth or read credential contents. Another OAuth probe is
not required for follow-up changes limited to path validation, test
synchronization, documentation, or M2 parser work.

## Remediation Status

All required follow-ups (F1–F4) remediated:
- **F1 (Symlink hardening):** Rejects symlinked profiles root and credential subtree (`antigravity-cli`), validates layout before executable resolution, with external-target, real-home, and cross-profile regression tests.
- **F2 (Deterministic concurrency proof):** Concurrency test uses ready-file barrier polling (`ready_a`, `ready_b`) proving simultaneous liveness with safe duration `< 0.75s` and alias assertions.
- **F3 (Strict CLI parsing):** Top-level and misplaced arguments rejected by parser with error 2; remainder args only forwarded after profile command.
- **F4 (Snapshot helper):** Cleanly integrated into `src/magy/testing/snapshot.py` with 0 Ruff violations and 100% test coverage in `tests/test_snapshot.py`.
- **Test suite:** 110 passed tests across Python 3.11 and 3.14 (`ruff check` clean, `uv build` clean).
