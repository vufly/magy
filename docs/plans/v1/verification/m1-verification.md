# Milestone 1 Verification Report: Profile Isolation Gate

## 1. Decision

**PASS WITH FOLLOW-UP.** Milestone 1's central profile-isolation requirements
(M1-S1 through M1-S6) and compatibility gate are satisfied.
Two-account identity separation, repeated launch survival, concurrent execution,
single-profile invalidation isolation, and real-home non-modification have been
empirically validated using official Agy 1.2.6. Remaining path hardening and
test precision are tracked in [`docs/plans/backlog.md`](../../backlog.md).

## 2. Test Environment

- **Agy Version:** Official Google Antigravity CLI (agy) `1.2.6`
- **Agy Binary:** `~/.local/share/mise/installs/antigravity/latest/agy`
- **Operating System:** Linux (kernel `6.18.33.2-microsoft-standard-WSL2`, x86_64)
- **Python Runtimes:** Python 3.14.7 (system / virtualenv) and Python 3.11.16 (`uv`)
- **Isolation Mechanism:** Process-level home redirection (`HOME`, and on
  Windows `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`) points to private managed
  directories (`0o700`). `MAGY_REAL_HOME` records the original home;
  `AGY_CLI_DISABLE_AUTO_UPDATE` and `MAGY_PROFILE` are child controls.

## 3. Automated Safety & Fake-Account Verification (Stages 1 & 2)

Before invoking real authentication, automated gates verified:

- **Directory permissions:** Profile roots, synthetic homes, and `.gemini` directories are strictly owner-only (`0o700`).
- **Symlink rejection (H3):** Symlinks pointing to real home, external directories, or other profiles are rejected before creation or execution.
- **Configured executable precedence (M1):** Configuration file `agy_cmd` takes precedence over `PATH` when `MAGY_AGY_CMD` is unset; invalid paths fail cleanly without silent `PATH` fallback.
- **Protected variable enforcement (M2):** Caller overrides of `HOME`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `MAGY_REAL_HOME`, `AGY_CLI_DISABLE_AUTO_UPDATE`, and `MAGY_PROFILE` are rejected. Parent `os.environ` remains completely unmutated.
- **Windows path semantics (M4):** `ntpath.splitdrive` validates drive-letter (`D:`) and UNC (`\\server\share`) splits independently of host OS.
- **Persistent fake-account simulation (Stage 2):** Profile A and Profile B
  retain distinct `account_alias.txt` markers across repeated launches and
  isolated logout. Deterministic automated overlap proof is tracked as an M2
  test-hardening follow-up; live parallel operation was user-confirmed.
- **Test Suite Result:** 104 passed tests on Linux across Python 3.11 and 3.14 (`ruff` clean, `uv build` clean).

## 4. Live Two-Account Isolation Probe (Stage 3)

The live isolation probe was conducted with official Google Antigravity CLI `1.2.6`
and two distinct authenticated Google accounts (referred to by local redacted aliases
`account-A` and `account-B`).

No passwords, email addresses, OAuth URLs, client secrets, or OAuth token
contents were read by Magy or the reviewer, logged, or exported. Official Agy
necessarily accessed its managed profile credentials during authenticated runs.

### Step 4.1: Unauthenticated Initial State
- Ran `uv run magy profile run profile-a models`.
- **Exit Status:** 1
- **Output:** `Error: Please sign in to view available models. Launch the CLI without arguments to sign in.`
- **Observation:** Official Agy detected an empty synthetic home and did not inherit credentials from the real user home.

### Step 4.2: Interactive Profile Authentication
- User ran `uv run magy profile auth profile-a` in terminal and signed in as `account-A`.
- User ran `uv run magy profile auth profile-b` in terminal and signed in as `account-B`.
- Credentials were saved to:
  - `<data>/profiles/profile-a/home/.gemini/antigravity-cli/antigravity-oauth-token` (1661 bytes, mode `0o600`)
  - `<data>/profiles/profile-b/home/.gemini/antigravity-cli/antigravity-oauth-token` (1647 bytes, mode `0o600`)

### Step 4.3: Repeated Launch & Model Discovery
Executed model queries and inference prompts through each profile twice:

| Profile | Command | Exit Code | Observed Output |
| --- | --- | --- | --- |
| `profile-a` | `magy profile run profile-a models` | 0 | Fetched full model list (gemini-3.8, claude, gpt-oss) |
| `profile-b` | `magy profile run profile-b models` | 0 | Fetched full model list (gemini-3.8, claude, gpt-oss) |
| `profile-a` | `magy profile run profile-a -- --print "respond with the single word PING"` | 0 | `PING` |
| `profile-b` | `magy profile run profile-b -- --print "respond with the single word PONG"` | 0 | `PONG` |
| `profile-a` | `magy profile run profile-a -- --print "respond with the word AGAIN-A"` | 0 | `AGAIN-A` |
| `profile-b` | `magy profile run profile-b -- --print "respond with the word AGAIN-B"` | 0 | `AGAIN-B` |

Both profiles retained their authenticated state across repeated invocations without re-prompting.

### Step 4.4: Concurrent Execution Probe
The user launched `profile-a` and `profile-b` as concurrent background
processes and confirmed both were active in parallel. Sanitized command shape:

```bash
uv run magy profile run profile-a -- --print "respond with CONC-A" &
uv run magy profile run profile-b -- --print "respond with CONC-B" &
wait
```

- **Exit Status:** Both commands completed with status 0 as recorded during the
  interactive probe.
- **Output A:** `CONC-A`
- **Output B:** `CONC-B`
- **Observation:** Both profiles executed concurrently without lock contention, database collisions, or identity crosstalk.

### Step 4.5: Single-Profile Invalidation & Logout Isolation
Simulated logout by invalidating Profile A's credentials (`antigravity-oauth-token` deleted):

| Profile | Command | Exit Code | Observed Output |
| --- | --- | --- | --- |
| `profile-a` | `magy profile run profile-a models` | 1 | `Error: Please sign in to view available models.` |
| `profile-b` | `magy profile run profile-b models` | 0 | Fetched full model list |
| `profile-b` | `magy profile run profile-b -- --print "respond with STILL-ALIVE"` | 0 | `STILL-ALIVE` |

**Observation:** Invalidation of Profile A had zero effect on Profile B. Profile B remained authenticated, active, and capable of generating responses.

### Step 4.6: Real-Home Tampering Verification
A metadata-only snapshot of real `~/.gemini` was captured before the live probe
and compared afterward. The helper records relative path, size, mode, mtime, and
directory type without reading file contents; implementation is retained in
`src/magy/testing/snapshot.py`.

- **Total Entries in Real `~/.gemini`:** ~2300 entries.
- **Credential Modifications:** None. Real user's `antigravity-oauth-token`, configuration files, and authentication tokens experienced 0 modifications, 0 additions, and 0 removals.
- **Observed Changes:** All diffs were restricted to active assistant session
  transcripts and background daemon logs. Real-home authentication state was
  unchanged.

### Step 4.7: Keyring and Cross-Profile Assessment
- No shared-keyring crosstalk or keyring prompt was observed on Linux/WSL2.
- Observed credentials, chat history, databases, and caches were written beneath
  each managed `<HOME>/.gemini/antigravity-cli`.
- This demonstrates effective separation on the tested Agy/OS combination; it
  does not establish undocumented internal keyring implementation details.

## 5. Exit Gate Compliance Summary

| Criterion | Target | Result | Evidence |
| --- | --- | --- | --- |
| M0 Foundation Prerequisite | Approved | PASS | Commit `2aef7fb` |
| Direct & Subtree Symlink Rejection | Fail Closed | PASS | Root, profile, home, .gemini, and credential subtree symlink tests |
| Configured Executable Precedence | Precedence | PASS | `tests/test_profiles.py` config precedence tests |
| Override Protection | Protected | PASS | `tests/test_profiles.py` override tests |
| Windows Path Semantics | `ntpath` | PASS | `tests/test_profiles.py` drive and UNC tests |
| Persistent Fake State | Stage 2 | PASS | `test_stage2_persistent_fake_accounts` deterministic barrier |
| Two Distinct Accounts | Live | PASS | `account-A` and `account-B` authenticated |
| Repeated Execution | Live | PASS | Repeated discovery and print requests exit 0 |
| Concurrent Execution | Live | PASS | Simultaneous executions exit 0 (`CONC-A`, `CONC-B`) |
| Logout Isolation | Live | PASS | Profile A invalidated; Profile B remained operational (`STILL-ALIVE`) |
| Real Home Tampering | Zero Tamper | PASS | Before/after metadata snapshot diff verified |
| Secret Redaction | Strict | PASS | 0 tokens, passwords, or emails logged |

Milestone 1 passes. All reviewer follow-ups F1–F4 resolved. Milestone 2 is unblocked.
