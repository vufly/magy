# Backlog

## Cross-platform CI verification

- **Origin:** M0 review G3
- **Status:** Deferred; not an M0 or M1 gate
- **Target:** Before v1 release

Run the existing GitHub Actions matrix on native macOS and Windows runners for
Python 3.11 and 3.14. Confirm the full suite passes, with specific evidence that
Fake Agy captures selected environment variables and handles cancellation on
both operating systems. Record and remediate any platform-specific failures.

Linux coverage remains required during current milestone development. Native
macOS and Windows verification should resume when suitable runners are
available. See [`docs/guides/windows-testing.md`](../guides/windows-testing.md) for manual Windows verification steps.

## M1 follow-up hardening

- **Origin:** M1 review follow-ups F1-F4
- **Status:** Resolved in M1 closeout
- **Target:** Verified before M2

1. [x] Reject symlinked managed profiles roots and links through the known
   `.gemini/antigravity-cli` credential subtree. Added external, real-home, and
   cross-profile regression tests.
2. [x] Replaced elapsed-time-only fake concurrency coverage with deterministic
   simultaneous-liveness barrier evidence.
3. [x] Implemented strict Magy CLI option parsing rejecting unknown options before
   forwarding args.
4. [x] Integrated metadata snapshot tool cleanly at `src/magy/testing/snapshot.py`
   with full unit test coverage.
5. [ ] Native Windows junction/reparse behavior tracked under cross-platform CI verification.
