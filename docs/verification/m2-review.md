# Milestone 2 Final Re-review

## Decision

**Pass with cross-platform follow-up.** All previously reproduced Linux M2
correctness and security defects are fixed. The complete suite passes on the minimum
supported Python 3.11 and current Python 3.14, lint and formatting pass, and package
build succeeds.

User-confirmed live authentication remains successful. Milestone 3 may proceed.
Native Windows and macOS verification remains required during M4 hardening.

## Findings

No remaining release-blocking findings were identified in the reviewed Linux M2
scope.

## Resolved Findings

### Process and terminal lifecycle

- Interactive POSIX children run in a dedicated process group only when Magy owns
  the foreground job. Terminal foreground ownership is handed to the child and
  restored safely.
- Background jobs and shell pipelines do not steal terminal ownership.
- Stopped interactive children preserve foreground/background resume semantics.
- Timeout and forwarded-signal cleanup escalates through the complete process tree.
- Per-run `MAGY_RUN_ID` tagging finds double-forked and `setsid()` descendants on
  Linux without imposing a profile concurrency limit.
- Descendant cleanup remains scoped to the originating run during concurrent
  launches.

### Settings synchronization

- POSIX roots are opened component-by-component with directory descriptors and
  `O_NOFOLLOW`, including the synchronization lock path.
- Root replacement fails closed instead of falling back to pathname copying.
- Destination file and directory symlinks reject launch rather than remaining
  reachable by Agy.
- Legacy `config/skills` links are removed only through anchored descriptor-relative
  operations.
- External source skills links are rejected; contained skills are copied.
- Python 3.11 and 3.12 compatibility no longer depends on `os.lstat` appearing in
  `os.supports_dir_fd`.
- Windows retains managed-launch functionality with reparse-point rejection and
  atomic temporary-file replacement.

### Persistent state and redaction

- Future unknown failures persist only controlled generic reasons. Raw provider text
  remains in private run logs, not profile metadata.
- Legacy free-form cooldown reasons migrate to a controlled generic reason, removing
  previously persisted secret formats regardless of provider spelling.
- Direct sanitization covers complete Authorization headers, provider-prefixed
  fields, quoted values, AWS SigV4, and JSON/Python-style key-value text.
- Profile removal cleans private run logs.
- Failed staged-directory or state cleanup remains retryable through the same
  `profile remove` command; profile recreation is blocked until cleanup completes.

### Routing and CLI lifecycle

- Selection records and verifies profile incarnation atomically, rejecting
  remove/recreate ABA races.
- Launch verifies the selected incarnation again after acquiring the shared profile
  lease.
- Selection/removal and lease-contention errors produce controlled CLI diagnostics
  instead of tracebacks.
- Unregistered auth/run names remain rejected without profile-home creation.

## Verification

| Command or check | Result |
| --- | --- |
| `uv run pytest -q` | PASS, 222 tests on Linux/Python 3.14.7 |
| `uv run --python 3.11 pytest -q` | PASS, 222 tests on Linux/Python 3.11.16 |
| `uv run ruff check .` | PASS |
| `uv run --python 3.11 ruff check .` | PASS |
| `uv run ruff format --check .` | PASS, 46 files formatted |
| `uv build` | PASS, wheel and source distribution built |
| Controlling-terminal authentication regression | PASS |
| Inherited and captured timeout descendant regressions | PASS |
| Double-forked `setsid()` descendant cleanup | PASS |
| Concurrent profile launches | PASS |
| Source-root and destination-symlink sync regressions | PASS |
| Legacy secret-reason migration | PASS |
| Removal cleanup retry and private-log cleanup | PASS |
| Selection remove/recreate incarnation rejection | PASS |

## Residual Risks

- Native Windows process-tree, lifecycle-lock contention, reparse-point race, and ACL
  behavior were not executed in this Linux review.
- Native macOS/BSD process discovery and job-control behavior were not executed.
- Linux detached-process discovery uses `/proc` run markers; non-Linux POSIX systems
  retain process-group and parent/child discovery but do not have equivalent marker
  scanning.

These are explicit M4 cross-platform verification items rather than Linux M2 gate
blockers.
