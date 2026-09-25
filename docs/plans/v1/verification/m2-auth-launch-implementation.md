# Milestone 2 Follow-up Implementation: Focused Auth-Launch Remediation

## 1. Scope and Implementation Summary

This implementation delivers Option 1 (Focused auth-launch remediation)
to resolve issues H6, H7, and H8 (direct executable discovery, resolver protocol, stream inheritance).

### M2-A1 & M2-A2: Direct Executable Discovery and Resolver Configuration
- **Preserved Lexical Candidate Identity**:
  - Replaced unconditional canonicalization (`Path.resolve()`) during executable candidate discovery with lexical expansion (`safe_expand_path` / absolute pathing).
  - Candidates retain their lexical invocation path (`argv[0]`) so multicall tools (e.g. `mise`) do not have their internal dispatch altered by symlink target resolution.
- **Indirection & Multicall Classification**:
  - Implemented `classify_candidate(path: Path) -> tuple[bool, str | None]` in `src/magy/agy.py`.
  - Classifies regular files, broken symlinks, non-executable files, and symlink indirection/multicall shims without executing the candidate or invoking external toolchains.
  - PATH discovery (`resolve_agy_executable()`) scans PATH directories and skips indirect shims, selecting the first direct Agy executable.
  - Rejects explicit indirect candidates in `MAGY_AGY_CMD` or config `agy_cmd` source-specifically with actionable diagnostic messages, without silent fallback to PATH.
- **Configured Lookup Resolver (`agy_resolver`)**:
  - Extended `MagyConfig` in `src/magy/config.py` with `agy_resolver: list[str] | None = None`.
  - Enforced mutual exclusion between `agy_cmd` and `agy_resolver` with validation requiring a non-empty list of non-empty strings.
  - Implemented `execute_resolver(resolver_argv, cwd, timeout)` running the lookup-only command without a shell under the caller's original environment and working directory.
  - Validates output: requires exactly one absolute direct executable path, rejecting relative paths, empty output, multi-line output, or indirect targets.
  - Executed dynamically on every doctor probe and profile launch; the resolved path is never cached in configuration or state, enabling tool upgrades and reshim operations to take effect immediately.
  - Tool-agnostic protocol: works with `["mise", "which", "agy"]`, `["asdf", "which", "agy"]`, or any custom lookup command without hardcoding tool-manager specific logic.
- **Profile Environment Isolation (POSIX XDG Roots)**:
  - Updated `build_profile_env()` in `src/magy/profiles.py` to derive missing POSIX `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and `XDG_STATE_HOME` inside the profile home.
  - Prevents tool managers or runtime shims from treating profile homes as uninitialized machines or falling back to the caller's real user roots.

### M2-A3: Direct Terminal Stream Inheritance
- **Restored Terminal Ownership**:
  - For `capture_output=False` launches (`magy profile auth`, `magy profile run`, and top-level passthrough), subprocess streams are directly inherited (`stdin=None, stdout=None, stderr=None`).
  - Removed pipe-forwarding threads and rolling stdout/stderr buffers from interactive execution paths, ensuring full TTY ownership, unbroken terminal query sequences, and native TUI rendering.
  - Redirected stdout remains direct and byte-for-byte scriptable; Magy diagnostics remain on stderr.
- **Private Log Injection and Health Classification**:
  - In inherited-stream mode, `update_health=True` or `inject_log_file=True` automatically injects a private run log argument (`--log-file <state-dir>/logs/<name>/<run_id>.log`) unless the caller already provided one.
  - Correctly handles `--` arguments: places injected `--log-file` before `--` when present.
  - Resolves relative caller `--log-file` paths against the child `cwd` (or current working directory).
  - Classifies health status from process exit code and bounded log file tail without piping interactive streams.

### M2-A4: Extended Fake Harness and Test Infrastructure
- **Fake Agy Capabilities**:
  - Added `--interactive-probe` / `interactive_probe` mode recording `isatty` status for stdin, stdout, and stderr to a file, emitting prompts, and reading responses.
  - Added `FAKE_AGY_WRITE_LOG` support to simulate Agy writing auth and quota diagnostic messages directly to `--log-file`.
  - Added multicall execution detection guarded by `FAKE_AGY_IS_MULTICALL=1` to detect forbidden launcher execution during tests.
  - Recorded `argv0` and child isolation variables without exposing credential contents.

---

## 2. Test and Verification Evidence

### Automated Test Suite
- **Pytest (Python 3.14)**:
  ```text
  186 passed in 17.81s
  ```
- **Pytest (Python 3.11)**:
  ```text
  186 passed in 17.96s
  ```
- **Ruff Lint & Format**:
  ```text
  uv run ruff check . -> All checks passed!
  uv run ruff format --check . -> 45 files already formatted
  ```

### New Test Coverage
1. `tests/test_discovery.py`:
   - `test_discovery_path_skips_indirect_and_selects_direct`: PATH skips indirect multicall first candidate and chooses direct candidate.
   - `test_discovery_indirect_only_path_fails_without_side_effects`: Indirect-only PATH fails with diagnostic error and never executes launcher.
   - `test_discovery_indirect_explicit_env_and_config_fail_without_fallback`: Explicit indirect configurations fail cleanly without fallback.
   - `test_discovery_resolver_success_and_dynamic_switch`: Verifies dynamic switching between v1 and v2 upon manager state fixture update without restart or config edit.
   - `test_discovery_resolver_failures`: Covers nonzero exit, empty output, multiple lines, relative paths, indirect outputs, and non-executable outputs.
   - `test_broken_symlink_candidate_handling`: Verifies broken explicit symlinks fail and broken PATH shims are skipped.
2. `tests/test_doctor.py`:
   - `test_doctor_reports_direct_path_and_never_probes_indirect`: Doctor reports direct candidate and never version-probes indirect shims.
   - `test_doctor_indirect_only_fails_without_invoking_launcher`: Doctor fails cleanly when only indirect shims exist.
   - `test_doctor_resolver_dynamic_upgrade_reporting`: Doctor re-executes resolver and reports updated binary and version.
3. `tests/test_profiles.py`:
   - `test_profile_environment_isolation_and_xdg_roots`: Verifies synthetic `HOME` and derived profile-local POSIX `XDG_*` roots.
   - `test_profile_launch_skips_indirect_and_prevents_side_effects`: Verifies direct Agy launch skips indirect shims without side effects.
   - `test_profile_explicit_xdg_env_preserved`: Verifies caller-provided explicit XDG variables remain untouched.
4. `tests/test_tty.py`:
   - `test_pty_interactive_auth_prompt_and_response`: Verifies PTY interactive auth prompt emission, TTY detection on fd 0/1/2, and response receipt.
   - `test_pty_passthrough_interactive`: Verifies top-level `--profile <name>` passthrough in interactive PTY mode.
   - `test_redirected_stdout_scriptability`: Verifies stdout contains only child output while Magy profile diagnostics are routed to stderr.
   - `test_inherited_mode_private_log_injection_and_classification`: Verifies private log injection and health classification in inherited mode.
   - `test_inherited_mode_relative_caller_log_resolved_against_cwd`: Verifies relative caller log paths resolve against child `cwd`.

---

## 3. Isolated Global UV Tool Installation Smoke Test

Conducted end-to-end smoke test using a temporary isolated UV tool directory (`UV_TOOL_DIR` and `UV_TOOL_BIN_DIR`) and built wheel (`dist/magy-0.1.0-py3-none-any.whl`).

**Verification Steps & Results**:
1. Installed built wheel into temporary environment:
   - Command: `uv tool install --force dist/magy-0.1.0-py3-none-any.whl`
   - Result: Successful install, isolated binary at `<temp>/bin/magy`.
2. Configured indirect launcher first on PATH (`<temp>/path_bin1/agy -> fake_manager`) with side-effect marker detection.
3. Configured lookup resolver (`agy_resolver: [python, resolver.py]`) pointing to direct `agy-v1`.
4. Executed `magy doctor --json`:
   - Result: Resolved `agy-v1` with version `1.0.0-v1`.
   - Side-effect marker: Not created (indirect launcher never executed).
5. Executed `magy profile create smoke-p` and `magy profile auth smoke-p -- --interactive-probe` under POSIX PTY:
   - Result: TTY prompt received before input, input sent, process exited 0.
   - Side-effect marker: Not created.
   - Profile home directory: Remained completely clean, without tool-manager directories (`.local/share/mise`, etc.).
6. Switched resolver fixture from `agy-v1` to `agy-v2` without modifying Magy configuration or reinstalling:
   - Executed `magy doctor --json`.
   - Result: Dynamically reported `agy-v2` with version `2.0.0-v2`.

---

## 4. Handoff for Independent Review

All automated verification gates and smoke tests for Option 1 have passed. Per project guidelines, M2 review status in [`m2-review.md`](m2-review.md) remains unchanged awaiting independent evaluation.
