# M4 Review: Hardening, Verification, and Release

**Reviewer:** Agent self-review  
**Commit:** `7a5c03f` (M4) vs base `2c49700` (M3 final)  
**Result:** PASS (Remediated)

---

## Summary

M4 successfully delivers all six steps of the hardening milestone. The test matrix passes (285/285 on Python 3.11 and 3.14), security review is thorough, the compatibility integration is correct, and the release artifacts are buildable and installable. One low-severity documentation mismatch in `README.md` (L1) was identified and remediated.

---

## Findings

### L1: README documents non-existent CLI commands [LOW] — REMEDIATED

**Location:** `README.md`, lines 105–111 (Default / Current Profile section)

```markdown
magy profile set-current work
magy profile current
```

Neither `set-current` nor `current` existed as profile subcommands. Actual subcommands are:
`add`, `create`, `auth`, `run`, `list`, `show`, `enable`, `disable`, `reset-health`, `remove`.

**Remediation:** Replaced the "Default / Current Profile" subsection with "Inspecting Profiles" using the valid `magy profile show <name>` command. Updated [`m4-implementation.md`](m4-implementation.md) command listing accordingly.

---

## Positive Findings

### M4-S1: Test Matrix
- 285 tests pass on both Python 3.11 and 3.14 with no skips or warnings.
- `ruff check` and `ruff format --check` clean across 57 files.
- New tests cover `evaluate_agy_compatibility()` with exact string assertions, unverified version path (text and JSON), `--json` null compatibility on no-exec, and all three `-p` argument forms (`-p name`, `-p=name`, `-pname`).
- MCP stdio test race fixed correctly: stream-then-close replaces `communicate()`, verified stable across 5 consecutive runs.

### M4-S2: Live Verification
- Live `magy doctor` confirmed real Agy 1.2.6 on resolver path with correct compatibility note.
- Live `magy_profiles` MCP invocation returned structured JSON from `magy-mcp` over stdio.
- `magy -p vu2371992 -- --version` executed in isolated synthetic home.

### M4-S3: Security Review
- All seven plan items addressed in [`docs/architecture/security.md`](../../../architecture/security.md):
  - Zero credential introspection.
  - Path traversal via `validate_identifier()`.
  - Symlink traversal prevention (allowlist, TOCTOU inode verification).
  - Restrictive permissions (`0o700`/`0o600`) and atomic writes.
  - Anti-PID-recycling UUID marker verification.
  - Prompt omission in status APIs.
  - Auto-approval caution block.

### M4-S4: User Documentation
- README overhauled from 17-line scaffold to full product guide.
- Installation (`uv tool install`), profile flows, round-robin + explicit `-p`, cooldown (correctly states 60s default), MCP setup for Codex and OpenCode, synthetic HOME caveat, and provider disclaimer all present.
- ~~Non-existent `set-current` / `current` commands (L1)~~ — remediated; replaced with `magy profile show <name>`.

### M4-S5: Compatibility Report & Doctor
- `evaluate_agy_compatibility()` correctly prefix-matches on `"1.2."`, `"1.1."`, `"1.0."` — avoids false positives like `1.20.0` (correctly unverified) and handles `v`-prefixed versions.
- `compatibility_note` only populated when executable is found; `None` propagated to JSON when Agy absent — correct behavior, tested.
- [`docs/architecture/compatibility.md`](../../../architecture/compatibility.md) documents OS matrix, Python version matrix, tested Agy versions, storage assumptions, and prominent undocumented-change warning.

### M4-S6: Release Artifacts
- `uv build` produces `dist/magy-0.1.0-py3-none-any.whl` and `dist/magy-0.1.0.tar.gz`.
- Clean venv smoke test verified both `magy --version` and `magy-mcp --help`.
- `uv tool install --force .` installs both executables.
- Added `magy-mcp --help` / `-h` support with test coverage.

---

## Exit Criteria Assessment

| Criterion | Status | Notes |
| :--- | :--- | :--- |
| Automated matrix passes | PASS | 285/285 on Python 3.11 and 3.14 |
| Live isolation and MCP checks pass | PASS | Doctor, profiles, passthrough, MCP verified live |
| No unresolved high-severity findings | PASS | 0 high findings; 1 low (L1 - remediated) |
| Verification report maps evidence to acceptance criteria | PASS | `m4-implementation.md` complete |
| Clean install exposes `magy` and `magy-mcp` | PASS | Wheel and tool install verified |

---

## Remediation Verification

- **L1 Remediation:** Replaced non-existent commands in [`README.md`](../../../../README.md) with `magy profile show <name>`. Updated [`m4-implementation.md`](m4-implementation.md).
- **Status:** All exit criteria satisfied. Ready to commit.
