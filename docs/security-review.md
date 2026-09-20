# Magy Security Review & Threat Model

This document details the security posture, threat model, boundaries, and hardening mechanisms implemented in Magy.

---

## 1. Security Principles and Trust Boundaries

Magy is a local process supervisor and MCP server that manages isolated Antigravity (`agy`) CLI profiles. It operates strictly within the privileges of the local user running the program.

### Core Security Guarantees
1. **Zero Credential Introspection**: Magy never reads, parses, logs, or copies authentication tokens, API keys, or session secrets.
2. **Strict Profile Containment**: Each profile operates inside an isolated synthetic home directory (`<data_dir>/profiles/<profile_name>/home`).
3. **Symlink Traversal Prevention**: Synchronization of user configurations (skills, rules, extensions) strictly validates paths and rejects symlinks that escape repository or profile boundaries.
4. **Tamper-Resistant Storage**: State, profile metadata, and configuration files enforce restrictive filesystem permissions (`0o700` directories, `0o600` files).
5. **Safe Process Lifecycle**: Cancellation and timeout signals target strictly validated process trees with anti-PID-recycling safeguards.

---

## 2. Threat Analysis and Hardening

### 2.1 Credential File Protection
- **Vulnerability**: Leakage or accidental exposure of session tokens in command outputs, error messages, or logs.
- **Hardening**:
  - `magy doctor` inspects filesystem permissions and executable paths without opening, inspecting, or printing credential file contents.
  - Test suites include explicit assertions checking that diagnostic outputs do not contain sensitive tokens or secret patterns.
  - Settings synchronization uses an explicit allowlist and explicitly excludes all credential stores (e.g. `credentials.json`, `auth.json`, tokens).

### 2.2 Path Traversal and Profile Identifier Injection
- **Vulnerability**: Path traversal attacks (e.g., `../../etc/shadow` or directory separators in profile or run names) leading to arbitrary file read/write.
- **Hardening**:
  - All profile and run identifiers are strictly validated via `validate_identifier()` in [`magy.storage`](file:///home/vudinhn/repos/magy/src/magy/storage.py).
  - Validation requires `^[a-zA-Z0-9_-]+$`, maximum length 64, non-empty, and explicitly rejects `.`, `..`, path separators (`/`, `\`), control characters, and Windows reserved device names (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`).
  - Storage paths are constructed using safe path joining and validated to ensure the resulting target is strictly nested under the intended root.

### 2.3 Settings Synchronization and Symlink Attacks
- **Vulnerability**: Symlinks in source `~/.gemini/` or destination profiles pointing to sensitive host directories, leading to file overwrite or arbitrary disclosure.
- **Hardening**:
  - Implemented in [`magy.settings_sync`](file:///home/vudinhn/repos/magy/src/magy/settings_sync.py).
  - Strict allowlist of top-level items: `GEMINI.md`, `antigravity.json`, `tools.json`, `mcp_config.json`, `extensions.json`, `keybindings.json`, `settings.json`, and directories `rules`, `skills`, `extensions`, `workflows`.
  - All source symlinks are resolved against the canonical source directory; symlinks pointing outside are skipped.
  - Destination paths are checked before writing: any symlink component encountered in the destination causes the operation to abort immediately.
  - Inode verification: File descriptors and realpaths are verified before and after traversal to detect symlink swap / time-of-check-time-of-use (TOCTOU) attacks.

### 2.4 Restrictive Permissions and Atomic File Operations
- **Vulnerability**: Other local users reading cached logs, run outputs, or configuration state on shared workstations.
- **Hardening**:
  - Storage roots and directories are initialized with POSIX mode `0o700` (`rwx------`).
  - Files and run payloads are created with POSIX mode `0o600` (`rw-------`).
  - Writes utilize atomic temporary files (`tempfile.mkstemp` inside the destination directory with restricted umask), followed by `os.replace` to prevent partial or corrupted writes.
  - Permissions are re-verified after creation, correcting or failing closed if the filesystem ignores mode requests.

### 2.5 Process Cleanup and PID Reuse
- **Vulnerability**: Process termination signals (`SIGTERM`/`SIGKILL`) targeting a PID that exited and whose ID was recycled by the operating system, killing an innocent process.
- **Hardening**:
  - Implemented in [`magy.worker`](file:///home/vudinhn/repos/magy/src/magy/worker.py) and [`magy.runs`](file:///home/vudinhn/repos/magy/src/magy/runs.py).
  - Each worker process writes an ephemeral UUID marker file to `/tmp/magy-worker-<pid>.marker` upon starting.
  - Before sending any signal to a process, Magy verifies that the marker file exists, is owned by the current user, and contains the expected run UUID.
  - Descendant processes are tracked through process group hierarchy and `/proc` process trees, ensuring orphaned child processes are cleanly reaped upon run cancellation or timeout.

### 2.6 MCP Tool Safety and Prompt Sanitization
- **Vulnerability**: Leaking raw user prompts or environment variables through status polling APIs or crash reports.
- **Hardening**:
  - `magy_run_status` reports run status, profile name, exit codes, timestamps, and error summaries without echoing the complete user prompt.
  - MCP responses limit message payloads to necessary metadata. Run output is retrieved only via explicit call to `magy_run_result`.

---

## 3. Prominent Auto-Approval Warning

> [!CAUTION]
> **MCP Auto-Approval Security Notice**
> By default, Magy executes Antigravity CLI runs requiring interactive confirmation for potentially destructive tool actions (such as filesystem modifications or terminal commands).
> While the MCP server schema supports an optional `auto_approve` flag for fully autonomous pipelines, **enabling auto-approval allows the model to execute commands and write files without human-in-the-loop review**.
> Use `auto_approve` only within sandboxed environments or when operating on throwaway branches.
