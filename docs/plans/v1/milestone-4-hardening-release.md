# M4: Hardening, Verification, And Release

## Goal

Complete cross-platform quality work, security review, user documentation, and
release readiness after functional milestones pass.

## Steps

### M4-S1: Automated test matrix

Run Ruff and pytest on Ubuntu, macOS, and Windows. Cover:

- Home environment construction.
- Settings allowlist and auth-file exclusion.
- Round-robin, cooldown, explicit selection, and concurrent cursor updates.
- Unlimited parallel selections.
- Health classification and latest-signal precedence.
- CLI stream/exit/signal behavior.
- Durable restart, timeout, cancellation, idempotency, and result chunking.
- MCP schema and stdio protocol smoke tests.

### M4-S2: Live verification

Repeat M1 account-isolation checks against the release candidate. Add one live
MCP print-mode run per profile. Keep tests opt-in and local; never run them in
public CI.

### M4-S3: Security review

Review:

- Credential files are neither read nor copied.
- Profile and run identifiers cannot escape storage roots.
- Settings synchronization cannot follow external symlinks.
- Private files use restrictive permissions where supported.
- Logs and MCP responses do not leak prompts or environment secrets by default.
- Cancellation targets only recorded process trees.
- Default auto-approval warning is prominent.

### M4-S4: User documentation

Write the product README with:

- Installation through `uv tool install` and local development setup.
- Profile add/auth/current registration flows.
- Explicit and automatic launcher examples.
- Known-health cooldown semantics and no-replay guarantee.
- Codex/OpenCode MCP setup.
- Synthetic-home caveat for commands that inspect `$HOME`.
- `magy doctor` troubleshooting.
- Provider terms and enforcement disclaimer.

### M4-S5: Compatibility report

Record tested Agy versions and OS versions. State that undocumented Agy storage
or keyring changes may break profile isolation. Make `magy doctor` identify
unsupported or unverified versions without claiming they are safe.

### M4-S6: Release artifacts

Build wheel and source distribution. Verify both console entry points from a
clean environment. Do not publish or tag unless explicitly requested.

## Exit Criteria

- Automated matrix passes on all target OSes.
- Release-candidate live isolation and MCP checks pass.
- Implementation review has no unresolved high-severity findings.
- Verification report maps evidence to every global acceptance criterion.
- Clean install exposes working `magy` and `magy-mcp` commands.

## Review Focus

- Documentation matches actual flags and defaults.
- Live verification evidence is redacted.
- Unsupported Agy versions fail safely or warn accurately.
- No release action occurs without explicit approval.
