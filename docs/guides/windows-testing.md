# Windows Verification and Testing Handoff

## Overview

This guide outlines the testing procedure for Magy on Windows (native PowerShell / Command Prompt).
It details automated validation, black-box CLI testing, live account authentication, and identifies which steps require manual user intervention.

---

## 1. Prerequisites on Windows

Ensure the following tools are installed on the Windows host:
1. **Git for Windows** (with SSH or HTTPS credentials configured).
2. **Python 3.11+** or **uv** (`powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"`).
3. **Antigravity CLI (`agy`)**:
   - Ensure `agy.cmd` or `agy.exe` is installed and accessible in `PATH` (e.g. `%USERPROFILE%\.local\bin\agy.cmd` or `%APPDATA%\npm\agy.cmd`).
   - Run `where.exe agy` in PowerShell to verify discovery.

---

## 2. Setup Repository

Open **PowerShell** or **Windows Terminal**:

```powershell
# Clone or pull latest master
git clone git@github.com:vufly/magy.git
cd magy
git checkout master
git pull origin master

# Create virtual environment and install in editable mode
uv venv
uv pip install -e ".[dev]"

# Alternatively install globally as a tool:
uv tool install --reinstall --editable .
```

---

## 3. Automated Test Suite Verification

Run the pytest suite to check platform-specific execution:

```powershell
uv run pytest
```

> [!NOTE]
> Some tests checking string path representations with backslashes on Windows may fail due to deferred backlog item ("Cross-platform CI verification" in [`docs/plans/backlog.md`](../plans/backlog.md)). Focus on core CLI, profile management, and live process isolation.

Run focused CLI and profile tests:

```powershell
uv run pytest tests/test_cli_m2.py tests/test_storage.py tests/test_discovery.py
```

---

## 4. Black-Box CLI & Health Verification

Run the following checks from PowerShell:

### Step 4.1: Doctor Diagnostics
```powershell
magy doctor
```
**Expected Outcome:**
- Reports platform as `Windows`.
- Resolves configuration, data, and state roots under `%LOCALAPPDATA%\magy` or `%USERPROFILE%\AppData\Local\magy`.
- Finds `agy` executable via `PATH` or resolver.
- Status displays `OK - all prerequisites satisfied`.

### Step 4.2: Help System
```powershell
magy help
magy help profile
magy profile add --help
magy profile help auth
```
**Expected Outcome:**
- Displays formatted usage text and examples without crashing or leaking passthrough arguments.

### Step 4.3: Status Inspection
```powershell
magy status
```
**Expected Outcome:**
- Displays Total, Enabled, Available, Healthy, Untested, In Cooldown counts.

---

## 5. Live Profile Authentication (MANUAL STEPS)

> [!IMPORTANT]
> **Manual User Action Required:**
> Interactive OAuth login requires user interaction in the web browser. The agent cannot complete Google OAuth credentials on your behalf.

### Step 5.1: Create Profile 1
```powershell
magy profile add win-account-1
```

### Step 5.2: Authenticate Profile 1 (MANUAL)
```powershell
magy profile auth win-account-1
```
**What you need to do manually:**
1. Magy launches `agy` in an isolated environment with redirected `USERPROFILE`, `HOME`, and `HOMEPATH`.
2. Follow the browser prompt or terminal URL provided by Agy.
3. Log into your first Google account.
4. Once completed, exit the session (`exit` or Ctrl+C).

### Step 5.3: Verify Profile 1 Isolation
```powershell
# Run a read-only command in profile 1
magy -p win-account-1 -- whoami
# or
magy -p win-account-1 -- models
```
Confirm `magy profile show win-account-1` shows `Health: healthy` and `Available: yes`.

### Step 5.4: Create Profile 2
```powershell
magy profile add win-account-2
```

### Step 5.5: Authenticate Profile 2 (MANUAL)
```powershell
magy profile auth win-account-2
```
**What you need to do manually:**
1. Follow the browser prompt.
2. Log into your **second** Google account.
3. Once completed, exit the session.

### Step 5.6: Test Round-Robin Switching
Execute two consecutive commands without `-p`:
```powershell
magy -- models
magy -- models
```
**Expected Outcome:**
- First execution selects `win-account-1` (outputs `[magy] using profile: win-account-1` to stderr).
- Second execution rotates to `win-account-2` (outputs `[magy] using profile: win-account-2` to stderr).
- Confirm host `%USERPROFILE%\.gemini` was **never** modified or overwritten.

---

## 6. MCP Server Validation on Windows

Test that `magy-mcp` works under stdio on Windows:

```powershell
# Test stdio launch
magy-mcp --help
```

In OpenCode or Codex configuration on Windows (`%APPDATA%\opencode\config.json` or `%USERPROFILE%\.config\opencode\config.json`):

```json
{
  "mcpServers": {
    "magy": {
      "command": "magy-mcp.exe"
    }
  }
}
```

Verify that tools (`magy_profiles`, `magy_run_start`, `magy_run_wait`, `magy_run_status`, `magy_run_result`, `magy_run_cancel`) appear in the client.

---

## 7. Summary: What Requires Manual Action

| Action | Automated / Scriptable | Manual User Intervention |
|---|---|---|
| Git pull & `uv tool install --editable .` | Yes | No |
| `magy doctor` & `magy status` | Yes | No |
| `magy profile add <name>` | Yes | No |
| **`magy profile auth <name>`** | **No** | **YES**: Requires browser Google OAuth login. |
| CLI execution & round-robin testing | Yes | No |
| Editor MCP JSON config editing | Semi-automated | **YES**: Add server entry to editor config if desired. |
