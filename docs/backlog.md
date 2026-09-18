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
available.
