# Active Engineering Backlog

This document tracks active, unresolved engineering backlog items and deferred verification tasks for Magy.

---

## Cross-Platform CI and Native OS Verification

- **Origin:** M0 review (G3), M2/M3 cross-platform reviews
- **Status:** Active / Deferred (requires dedicated native macOS and Windows runner environments)
- **Target:** Post-v1 hardening

### Scope

1. **Automated Matrix Verification:**
   - Execute the configured GitHub Actions test matrix on native macOS and Windows runners (Python 3.11 and 3.14).
   - Verify that fake Agy captures selected environment variables and handles process-tree cancellation cleanly on each platform.

2. **Native Windows Verification:**
   - Validate process-tree termination, `kernel32.LockFileEx` lifecycle locking, and console signal dispatch under native PowerShell and Command Prompt.
   - Confirm file ACL protections and Windows junction/reparse-point traversal safeguards.
   - For manual testing steps, see [`docs/guides/windows-testing.md`](../guides/windows-testing.md).

3. **Native macOS Verification:**
   - Validate POSIX permission enforcement, process-group signaling, and detached descendant cleanup without `/proc` markers.
