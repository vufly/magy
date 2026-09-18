# Milestone 0 Review: Foundation

### Decision

**Pass.** All findings (F1, F2, F3, G1, G2, R1, R2) are resolved.
Unknown-user path expansion produces bounded diagnostics without tracebacks
across all environment variables, storage roots, and executable paths. Private
POSIX permissions fail closed if `chmod` fails or if permissive bits remain,
and atomic writes clean up temporary files on permission failure.
89 tests pass on Linux across Python 3.11 and Python 3.14. Native macOS and
Windows execution remains deferred to `docs/backlog.md`. Milestone 0 is approved.

## Re-review Status

| Item | Status | Evidence |
| --- | --- | --- |
| F1: strict identifier validation | RESOLVED | Uses `fullmatch`; control-character regressions pass. |
| F2: partial atomic writes | RESOLVED | Complete-write loop and forced partial-write test pass. |
| F3: invalid configuration diagnostics | RESOLVED | Content/schema/lock/OS errors work; path expansion bounded. |
| G1: process concurrency | RESOLVED | Spawned-process update test passes on Linux. |
| G2: child-process test scope | RESOLVED | Test and documentation now describe PID tracking only. |
| G3: native CI evidence | DEFERRED | Tracked in `docs/backlog.md`; required before v1 release. |
| R1: storage-root error boundary | RESOLVED | Unknown-user expansion converted to bounded errors without traceback. |
| R2: private permissions | RESOLVED | POSIX mode enforcement fails closed; permissive paths rejected. |

## Original Findings

### F1: Identifier validation accepts a trailing newline

- **Severity:** Medium
- **Location:** `src/magy/storage.py:10`, `src/magy/storage.py:40`
- **Affected step:** M0-S4

`IDENTIFIER_PATTERN` uses `$` with `re.match`. In Python, `$` can match before a
final newline, so `validate_identifier("valid\n")` returns the input instead of
rejecting it. This contradicts the promised alphanumeric/dash/underscore
contract and permits invisible or log-injecting profile/run directory names on
POSIX.

Observed reproduction:

```text
uv run python -c "from magy.storage import validate_identifier; print(repr(validate_identifier('valid\\n')))"
'valid\n'
```

Required fix:

- Use `IDENTIFIER_PATTERN.fullmatch(name)` or an equivalent strict check.
- Add tests for trailing and embedded newline, carriage return, tab, and other
  control characters.

### F2: Atomic JSON replacement does not handle partial writes

- **Severity:** Medium
- **Location:** `src/magy/storage.py:101-109`
- **Affected step:** M0-S4

`os.write(fd, encoded)` is called once and its returned byte count is ignored.
`os.write` is allowed to write fewer bytes than requested without raising. In
that case Magy fsyncs and atomically replaces valid state with truncated JSON.
Every later profile cursor and durable run state depends on this helper.

Required fix:

- Write until all encoded bytes are consumed, or use a binary file object whose
  complete-write behavior is checked.
- Keep fsync before replacement.
- Add a test that monkeypatches the low-level write to return partial progress
  and verifies complete valid JSON reaches the destination.
- Consider fsyncing the parent directory after `os.replace` if crash durability,
  not only atomic visibility, is part of the intended contract.

### F3: Invalid configuration is hidden or can crash diagnostics

- **Severity:** Medium
- **Location:** `src/magy/config.py:58-77`, `src/magy/agy.py:44-51`,
  `src/magy/agy.py:99-107`
- **Affected step:** M0-S2

`load_config` catches every exception and silently returns defaults. Malformed,
unreadable, or lock-timed-out configuration can therefore make `magy doctor`
report healthy while ignoring the user's configured executable. Separately,
`MagyConfig.from_dict` accepts any JSON value for `agy_cmd`; a number, list, or
object reaches `Path(configured_cmd)` and crashes diagnostics with `TypeError`.

Required fix:

- Validate `agy_cmd` as `null` or a non-empty string.
- Preserve a bounded configuration error for `doctor` instead of silently
  treating invalid configuration as absent.
- Avoid broad `except Exception` where specific parse, I/O, validation, and lock
  errors can be reported safely.
- Add text and JSON doctor tests for malformed JSON, invalid `agy_cmd` type, and
  unreadable/lock failure where portable.

## Test And Evidence Gaps

### G1: Concurrent writer coverage is thread-only

`tests/test_storage.py:131-174` uses `ThreadPoolExecutor`. The implementation
report describes multi-process safety, and future CLI/MCP callers will be
separate processes. Add at least one spawned-process `update_json` test to prove
the file lock and atomic update behavior across processes.

### G2: Process-tree test verifies creation, not cancellation

`tests/test_fake_agy.py:105-139` terminates only the fake parent, then manually
kills the child without asserting its state. This is sufficient to show the
harness can spawn a child, but it does not verify process-tree cancellation.
Keep the full tree-kill assertion for M3 when Magy's cancellation implementation
exists. Rename or clarify the M0 test so it does not imply stronger coverage.

### G3: Native CI evidence is not available yet

The workflow defines Ubuntu, macOS, and Windows jobs, but implementation files
are currently untracked and no workflow result was supplied. M0's cross-platform
exit criterion remains unverified until CI runs from a commit or branch.

## Positive Review Notes

- Runtime dependency set matches the M0 plan.
- Executable discovery order matches `MAGY_AGY_CMD`, config, then `PATH`.
- Test autouse fixture removes real Agy discovery and isolates Magy storage
  roots.
- Fake Agy records only an explicit environment allowlist by default.
- M0 correctly avoids production profile, routing, and MCP behavior.
- Private POSIX modes are applied to current Magy root directories and JSON
  files.
- Build produces both wheel and source distribution successfully.

## Commands Run

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Python 3.14 environment before minimum-version run. |
| `uv run pytest` | PASS | 49 tests on Linux/Python 3.14.7. |
| `uv build` | PASS | Built wheel and source distribution. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python lint check. |
| `uv run --python 3.11 pytest` | PASS | 49 tests on Linux/Python 3.11.16. |
| Identifier newline reproduction | FAIL as expected | Confirmed F1. |

No real Agy command or credential file was used during review verification.

## Original Remediation Handoff

Implementation harness should complete these steps before requesting re-review:

1. Fix F1 with strict full-string identifier matching and control-character
   tests.
2. Fix F2 with complete-write handling and a forced partial-write regression
   test.
3. Fix F3 with typed config validation and surfaced doctor diagnostics.
4. Add a spawned-process storage update test for G1.
5. Clarify the fake child-process test name/assertions for G2.
6. Run Ruff and pytest on Python 3.11 and the default local Python.
7. Obtain Ubuntu, macOS, and Windows CI results after the implementation is
   committed or pushed.
8. Update `docs/verification/m0-implementation.md` with fixes and evidence.

Re-review should focus on the changed storage/config paths and confirm no M1
profile behavior was introduced early.

## Re-review Commands

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Linux/Python 3.14.7. |
| `uv run pytest` | PASS | 64 tests on Linux/Python 3.14.7. |
| `uv build` | PASS | Built wheel and source distribution. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python. |
| `uv run --python 3.11 pytest` | PASS | 64 tests on Linux/Python 3.11.16. |
| Storage-root error reproduction | FAIL as expected | Confirmed R1 traceback. |

No real Agy command or credential file was used during re-review verification.

## Final Review Commands

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Linux/Python 3.14.7. |
| `uv run pytest` | PASS | 68 tests on Linux/Python 3.14.7. |
| `uv build` | PASS | Built wheel and source distribution. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python. |
| `uv run --python 3.11 pytest` | PASS | 68 tests on Linux/Python 3.11.16. |
| Unknown-user config-root reproduction | FAIL as expected | Confirmed R1 traceback. |
| Forced `chmod` failure reproduction | FAIL as expected | Confirmed R2 leaves mode `0o777`. |

No real Agy command or credential file was used during final review
verification.

## Remaining Handoff

Keep remediation inside M0 foundation scope. Do not add profile lifecycle,
routing, or MCP behavior.

### R1 implementation guidance

1. Treat `RuntimeError` from `Path.expanduser()`/path resolution as an expected
   diagnostic failure alongside `OSError`.
2. Cover all path boundaries:
   - `MAGY_CONFIG_DIR`, `MAGY_DATA_DIR`, and `MAGY_STATE_DIR`.
   - `MAGY_AGY_CMD`.
   - Configured `agy_cmd`.
3. Keep failures source-specific: configuration errors remain `config_error`,
   storage-root errors become missing prerequisites, and executable errors use
   the existing discovery result. Do not replace these boundaries with a broad
   catch that hides unrelated programming errors.
4. Add parameterized unknown-user (`~__magy_missing_user__`) regressions. Verify
   text and JSON doctor output returns 1, is unhealthy, contains a bounded error,
   and emits no traceback.

### R2 implementation guidance

1. On POSIX, do not suppress `chmod` failure in
   `ensure_private_directory()` or `ensure_private_file()`.
2. After enforcement, verify effective modes with `stat.S_IMODE`: directories
   must be `0o700` and files must be `0o600`. Surface failure if the filesystem
   does not apply the requested private mode.
3. Keep Windows behavior unchanged because POSIX mode enforcement is not
   available there.
4. Ensure `doctor` converts root permission failures into unhealthy diagnostics.
   Storage writes should fail before replacing destination state when private
   permissions cannot be established.
5. Add regressions for:
   - Existing permissive directories/files being corrected.
   - Forced `chmod` `PermissionError`/`OSError` propagation.
   - Doctor text and JSON output for root permission-enforcement failure.
   - Atomic-write cleanup when temporary-file permission enforcement fails.

### Exit verification

Run without invoking real Agy or reading credential files:

```sh
uv run ruff check .
uv run pytest
uv run --python 3.11 ruff check .
uv run --python 3.11 pytest
uv build
```

Then:

1. Update `docs/verification/m0-implementation.md` with R1/R2 fixes, test counts,
   and build evidence.
2. Request final M0 re-review.
3. Approve M0 only when R1 and R2 reproductions return bounded failures and all
   checks pass.
4. Commit the approved M0 implementation before starting M1.

Native macOS and Windows CI verification is tracked separately in
`docs/backlog.md` and no longer blocks M0.
