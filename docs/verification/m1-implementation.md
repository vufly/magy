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
  - Verified private modes (`0o700`), symlink rejection, contained paths, protected isolation variables, configured executable precedence, and parent environment immutability.
  - Snapshot tool created (`gemini_meta_snapshot.py`) to record metadata-only before/after snapshots of real `~/.gemini`.

- **Stage 2: Persistent Fake-Account Verification**
  - Extended `src/magy/testing/fake_agy.py` with harmless profile-local state stored at `$HOME/.gemini/account_alias.txt` without naming or modeling OAuth tokens.
  - Added `--record-alias <alias>`, `--logout`, `whoami` / `--whoami`, `--sleep <sec>`, and `FAKE_AGY_REQUIRE_AUTH`.
  - Added test `test_stage2_persistent_fake_accounts` proving:
    1. Profile A and Profile B record and store different aliases in disjoint homes.
    2. Repeated launches reuse each profile's alias via `whoami`.
    3. Concurrent sleeping launches overlap in time while retaining their intended identities.
    4. Logging out Profile A deletes its alias while leaving Profile B completely intact.
    5. Parent environment and real-home state remain unpolluted.

## 3. Commands Run and Pass/Fail Results

| Command | Result | Notes |
| --- | --- | --- |
| `uv run ruff check .` | PASS | Python 3.14 linting passed with 0 errors. |
| `uv run pytest` | PASS | 104 passed in 10.40s on Python 3.14.7. |
| `uv run --python 3.11 ruff check .` | PASS | Minimum supported Python lint check passed. |
| `uv run --python 3.11 pytest` | PASS | 104 passed in 10.21s on Python 3.11.16. |
| `uv build` | PASS | Successfully built source distribution and wheel. |
| H3 symlink rejection tests | PASS | Verified symlinks to real-home, another profile, or external paths are rejected. |
| M1 configured executable tests | PASS | Verified `config.json` `agy_cmd` precedence and invalid-path rejection. |
| M2 protected variable override tests | PASS | Verified all 7 protected keys reject caller override attempts. |
| M4 Windows path semantics tests | PASS | Verified drive-letter (`D:`) and UNC paths (`\\server\share`) with `ntpath`. |
| Stage 2 fake-account persistence tests | PASS | Verified distinct identities, persistence across launches, concurrency, and logout isolation. |
| Official Agy 1.2.6 unauthenticated probe | PASS | `profile-a` with official `agy models` failed with `Error: Please sign in`, proving it does not read real `~/.gemini` credentials. |

## 4. Security-Sensitive Paths Touched

- Profile storage paths created:
  - `<magy-data>/profiles/<name>/` (`0o700`)
  - `<magy-data>/profiles/<name>/home/` (`0o700`)
  - `<magy-data>/profiles/<name>/home/.gemini/` (`0o700`)
- Verified that official `agy` under `profile-a` placed its generated files (logs, databases, cache) under `<magy-data>/profiles/profile-a/home/` and did NOT write to real `~/.gemini`.
- No token contents, client secrets, passwords, or emails were read, copied, logged, or recorded.

## 5. Stage 3 Instructions: Guided Live Verification

Stage 1 and Stage 2 automated safety gates have passed.
Stage 3 requires interactive authentication by the user in a terminal:

1. **Profile A authentication:**
   In your terminal, execute:
   ```bash
   uv run magy profile auth profile-a
   ```
   Follow the Google OAuth prompt and sign in with your first test account.
2. **Profile B authentication:**
   In your terminal, execute:
   ```bash
   uv run magy profile auth profile-b
   ```
   Follow the Google OAuth prompt and sign in with your second (distinct) test account.
3. **Notify Agent:**
   Confirm when both sign-ins are complete (using redacted aliases `account-A` and `account-B`). The agent will then run automated verification:
   - Repeated launch verification on both profiles.
   - Concurrent execution verification.
   - Single-profile logout isolation verification.
   - Metadata comparison of real `~/.gemini` to verify zero tampering.
   - Final Stage 4 compatibility report in `docs/verification/m1-verification.md`.
