# Milestone 1 Implementation Report: Profile Isolation Gate

## 1. Milestone and Commit/Worktree State

- **Milestone:** M1 (Profile Isolation Gate)
- **Base Commit:** `2aef7fb` ("Resolve Milestone 0 findings R1 and R2 and mark M0 approved")
- **Worktree State:**
  - Modified: `src/magy/cli.py`
  - Modified: `src/magy/testing/fake_agy.py`
  - Created: `src/magy/profiles.py`
  - Created: `tests/test_profiles.py`

## 2. Completed Step IDs and Remediation

- **M1-S1: Managed profile launcher and isolation controls**
  - Implemented `src/magy/profiles.py`:
    - `get_profiles_dir()`: returns `<data_dir>/profiles` with owner-only mode (`0o700`).
    - `get_profile_dir(name)`: validates identifier and returns `<data_dir>/profiles/<name>`.
    - `get_profile_home_dir(name)`: returns `<data_dir>/profiles/<name>/home`.
    - `ensure_profile_layout(name)`: creates profile directory, synthetic home directory, and `.gemini` with `0o700` permissions.
    - **H3 Remediation (Symlink traversal defense):** Implemented `_check_no_symlink_and_contained()`. Fails closed if profile directory, home directory, or `.gemini` is a symlink or resolves outside `<data_dir>/profiles`.
    - **M1 Remediation (Configured Agy executable precedence):** In `run_in_profile()`, loads configuration with `load_config_result()` and resolves executable following `MAGY_AGY_CMD`, configured `agy_cmd`, then `PATH`. Surfaces configuration errors without silent PATH fallback.
    - **M2 Remediation (Protected isolation variables):** Defined `PROTECTED_ENV_VARS` (`HOME`, `USERPROFILE`, `HOMEDRIVE`, `HOMEPATH`, `MAGY_REAL_HOME`, `AGY_CLI_DISABLE_AUTO_UPDATE`, `MAGY_PROFILE`). Rejects any attempt to override these variables in `run_in_profile()`. Ensures parent `os.environ` remains completely unmutated.
    - **M4 Remediation (Windows path semantics):** Implemented `apply_home_to_env()` using `ntpath.splitdrive` so Windows drive letters and UNC paths are parsed with Windows path semantics regardless of the host OS.
    - `run_in_profile(name, args, ...)`: launches resolved `agy` executable with the profile's isolated environment, preserving standard I/O streams and child exit codes.

- **M1-S2: Profile bootstrap and CLI**
  - Updated `src/magy/cli.py`:
    - **M3 Remediation (Clean M1 CLI):** Removed premature top-level `--profile` passthrough scanning. Implemented explicit M1 subcommands:
      - `magy profile create <name>`: bootstraps directory layout with private mode and symlink checks.
      - `magy profile auth <name> [args...]`: launches `agy` interactively with profile environment for visible user authentication.
      - `magy profile run <name> [args...]`: runs `agy [args...]` under the profile environment, forwarding child exit codes and preserving CLI arguments.

- **Stage 1: Automated Safety Gate Verification**
  - Verified private modes (`0o700`), direct profile/home/.gemini symlink rejection, contained profile paths, protected isolation variables, configured executable precedence, and parent environment immutability.
  - Metadata-only snapshot helper retained at `src/magy/testing/snapshot.py` for before/after comparison of real `~/.gemini` without reading file contents.

- **Stage 2: Persistent Fake-Account Verification**
  - Extended `src/magy/testing/fake_agy.py` with harmless profile-local state stored at `$HOME/.gemini/account_alias.txt` without naming or modeling OAuth tokens.
  - Added `--record-alias <alias>`, `--logout`, `whoami` / `--whoami`, `--sleep <sec>`, and `FAKE_AGY_REQUIRE_AUTH`.
  - Added test `test_stage2_persistent_fake_accounts` proving:
    1. Profile A and Profile B record and store different aliases in disjoint homes.
    2. Repeated launches reuse each profile's alias via `whoami`.
    3. Concurrent fake launches retain intended aliases. Deterministic automated overlap proof is tracked as an M2 follow-up; live overlap was user-confirmed.
    4. Logging out Profile A deletes its alias while leaving Profile B completely intact.
    5. Parent environment and real-home state remain unpolluted.

## 3. Commands Run and Pass/Fail Results

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Python 3.14 linting passed with 0 errors. |
| `uv run pytest` | PASS | 110 passed in 11.69s on Python 3.14.7. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python lint check passed. |
| `uv run --python 3.11 pytest` | PASS | 110 passed in 12.47s on Python 3.11.16. |
| `uv build` | PASS | Successfully built source distribution and wheel. |
| H3 & F1 symlink rejection tests | PASS | Verified symlinks to real-home, another profile, external paths, profiles root, and credential subtree are rejected. |
| M1 configured executable tests | PASS | Verified `config.json` `agy_cmd` precedence and invalid-path rejection. |
| M2 protected variable override tests | PASS | Verified all 7 protected keys reject caller override attempts. |
| M4 Windows path semantics tests | PASS | Verified drive-letter (`D:`) and UNC paths (`\\server\share`) with `ntpath`. |
| Stage 2 fake-account persistence tests | PASS | Verified distinct aliases, persistence, deterministic concurrency barrier overlap, and logout isolation. |
| Official Agy 1.2.6 unauthenticated probe | PASS | `profile-a` with official `agy models` required sign-in, showing real-home authentication was not reused. |

## 4. Security-Sensitive Paths Touched

- Profile storage paths created:
  - `<magy-data>/profiles/<name>/` (`0o700`)
  - `<magy-data>/profiles/<name>/home/` (`0o700`)
  - `<magy-data>/profiles/<name>/home/.gemini/` (`0o700`)
- Verified that official `agy` under `profile-a` placed its generated files (logs, databases, cache) under `<magy-data>/profiles/profile-a/home/` and did NOT write to real `~/.gemini`.
- Magy and reviewer did not read, copy, log, or record token contents, client secrets, passwords, or emails. Official Agy accessed managed credentials during authenticated use.

## 5. Stage 3 Live Verification Results

Stage 3 guided live verification was executed with official Agy 1.2.6 after user authentication:

1. **User Authentication:**
   - User authenticated `profile-a` via `uv run magy profile auth profile-a` with test account `account-A`.
   - User authenticated `profile-b` via `uv run magy profile auth profile-b` with test account `account-B`.
   - Credentials written to:
     - `<data>/profiles/profile-a/home/.gemini/antigravity-cli/antigravity-oauth-token` (1661 bytes, mode `0o600`)
     - `<data>/profiles/profile-b/home/.gemini/antigravity-cli/antigravity-oauth-token` (1647 bytes, mode `0o600`)
2. **Repeated Launches:**
   - `magy profile run profile-a models` -> exit 0, model list fetched.
   - `magy profile run profile-b models` -> exit 0, model list fetched.
   - `magy profile run profile-a -- --print "respond with the single word PING"` -> exit 0, `PING`.
   - `magy profile run profile-b -- --print "respond with the single word PONG"` -> exit 0, `PONG`.
   - `magy profile run profile-a -- --print "respond with the word AGAIN-A"` -> exit 0, `AGAIN-A`.
   - `magy profile run profile-b -- --print "respond with the word AGAIN-B"` -> exit 0, `AGAIN-B`.
3. **Concurrent Execution:**
   - Launched `profile-a` and `profile-b` simultaneously via background processes.
   - User confirmed both ran in parallel and completed with status 0, outputting `CONC-A` and `CONC-B` without observed identity crosstalk.
4. **Single-Profile Invalidation & Logout Isolation:**
   - Invalidated `profile-a` by unlinking its isolated token file.
   - Probed `profile-a models`: returned exit code 1 with `Error: Please sign in to view available models.`
   - Probed `profile-b models`: returned exit code 0 with full model list.
   - Probed `profile-b --print`: returned exit code 0 with `STILL-ALIVE`.
   - Proved complete isolation: invalidation of Profile A had zero impact on Profile B.
5. **Real-Home Non-Modification:**
   - Metadata comparison of real `~/.gemini` before and after all live probes showed 0 modifications, 0 additions, and 0 removals to user credentials or configuration.
6. **Final State:**
   - User re-authenticated `profile-a` so both `profile-a` and `profile-b` are active and available for reviewer evaluation.

## 6. Manual Actions Completed

- Two Google test accounts authenticated interactively in terminal by user.
- Logout isolation approval granted and verified.
- `profile-a` re-authenticated by user.
- Zero secrets, token contents, or credential contents recorded or exported.

## 7. Known Limitations and Next Milestone Prerequisites

- Round-robin routing, health tracking, cooldowns, and CLI passthrough are scheduled for Milestone 2.
- Hardening follow-ups F1 (profiles root/subtree symlink checks & regressions), F2 (deterministic concurrency barrier), F3 (strict CLI argument parsing), and F4 (clean snapshot tool integration) have all been implemented and verified.
- Milestone 1 passes. Milestone 2 is unblocked.
