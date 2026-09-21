# Magy

**Magy** is a multi-profile orchestrator, resilient runner, and Model Context Protocol (MCP) server for the Google Antigravity CLI (`agy`).

It enables seamless rotation across multiple authenticated accounts, round-robin load distribution with automated cooldown on rate limits, and durable, detached execution of agent tasks through CLI and MCP interfaces.

---

## Key Features

- **Isolated Profiles**: Runs each Agy account within an isolated synthetic home directory. Profiles retain their own authentication, tokens, and local cache without cross-contamination.
- **Settings Synchronization**: Automatically copies your skills, rules, workflows, and extensions from your real environment into each profile while excluding credential stores.
- **Intelligent Routing & Cooldown**: Automatic round-robin distribution with rate-limit and quota backoff.
- **No-Replay Guarantee**: Prompts are never silently replayed on failure, preventing duplicate side effects or token waste.
- **Durable MCP Server**: Run background tasks through Codex or OpenCode that survive MCP client reconnections, timeouts, and restarts.
- **Diagnostic Doctor**: Comprehensive self-test (`magy doctor`) validating executable paths, configuration, permissions, and Agy version compatibility.

---

## Installation

### Recommended: `uv tool install`

Install globally using [uv](https://docs.astral.sh/uv/):

```bash
# Install from local clone or git repository
uv tool install --from . magy

# Or install directly from GitHub
uv tool install git+https://github.com/vufly/magy.git
```

### Local Development Setup

Clone the repository and set up a virtual environment:

```bash
git clone https://github.com/vufly/magy.git
cd magy

# Install dependencies and pre-commit hooks
uv sync
uv run pytest
```

---

## Quickstart

### 1. Verify Environment

Check that Magy can locate your Antigravity executable and that all storage directories have secure permissions:

```bash
magy doctor
```

### 2. Add and Authenticate Profiles

Add profiles for each of your accounts. You can create a new profile or import an existing Agy configuration:

```bash
# Create a new profile and log in interactively
magy profile create work
magy profile auth work

# Add a personal account
magy profile create personal
magy profile auth personal

# View configured profiles
magy profile list
```

### 3. Check Routing Status

```bash
magy status
```

---

## Usage

### Running Commands

#### Automatic Profile Selection (Round-Robin)

When no profile is specified, Magy automatically rotates between healthy profiles:

```bash
magy -- "Review the latest commit on branch main"
```

#### Explicit Profile Selection

Specify a profile using the `-p` / `--profile` flag:

```bash
magy -p work -- "Investigate the memory leak in the billing service"
```

#### Inspecting Profiles

Inspect details and health for a specific profile:

```bash
magy profile show work
```

#### Getting Help

Magy provides comprehensive command documentation and examples at every level:

```bash
# Top-level help and available commands:
magy help
magy --help

# Subcommand help:
magy help profile
magy help doctor
magy help status

# Deeper action help:
magy help profile add
magy profile add --help
magy profile help auth
```

---

## Routing and Cooldown Semantics

- **Round-Robin**: Healthy, enabled profiles are dispatched sequentially.
- **Automated Cooldown**:
  - If a profile encounters rate limiting or temporary quota exhaustion, it enters cooldown (default: 60s) and Magy automatically advances to the next available healthy profile for future runs.
  - If a profile encounters an authentication failure, it is marked unhealthy until re-authenticated with `magy profile auth <name>`.
- **No-Replay Guarantee**:
  - If a command fails mid-execution or encounters rate limits, Magy **never** silently resubmits the prompt to another profile. Silent retries could cause duplicate side effects (e.g. creating tickets, pushing commits, modifying files). Instead, Magy fails fast, records the profile cooldown, and outputs a retryable error suggesting alternate available profiles.

---

## Model Context Protocol (MCP) Server

Magy includes a durable, stdio-based MCP server (`magy-mcp`) allowing agents in **Codex**, **OpenCode**, or **Claude** to launch and manage Antigravity tasks.

### Features
- **Detached Execution**: Worker runs execute detached from the MCP server. Reconnecting or restarting your editor does not kill in-flight jobs.
- **Status & Results**: Poll progress, stream logs, or wait synchronously with short-polling.
- **Process Cleanup**: Cancelling a run terminates the worker process and its entire descendant process tree cleanly.

### Configuration

#### OpenCode (`~/.config/opencode/config.json`)
```json
{
  "mcpServers": {
    "magy": {
      "command": "magy-mcp",
      "args": []
    }
  }
}
```

#### Codex (`~/.codex/config.json`)
```json
{
  "mcpServers": {
    "magy": {
      "command": "magy-mcp"
    }
  }
}
```

### Available MCP Tools

| Tool | Description |
| :--- | :--- |
| `magy_run_start` | Queue and start a prompt execution. Supports optional profile name and timeout. |
| `magy_run_status` | Retrieve execution state, running time, exit status, and error classification. |
| `magy_run_wait` | Wait for a run to finish with timeout and configurable polling interval. |
| `magy_run_result` | Retrieve full stdout, stderr, or structured output once completed. |
| `magy_run_cancel` | Terminate an in-flight run and clean up all child processes. |
| `magy_profiles` | List configured profiles and their current health status. |

For detailed documentation, see [`docs/mcp-configuration.md`](docs/mcp-configuration.md).

---

## Important Caveats

### Synthetic `$HOME` Environment
To guarantee profile isolation, Magy sets `$HOME` and `USERPROFILE` to the profile's dedicated synthetic home directory during execution (e.g., `~/.local/share/magy/profiles/<name>/home`).

Commands executed by Agy or child tools that inspect `$HOME` will observe this synthetic home rather than your primary system home. Settings synchronization automatically projects skills, rules, and global instructions from your host `~/.gemini/` into each profile.

### Provider Terms and Policy Notice
Magy is an independent orchestration utility and is not affiliated with or endorsed by Google. Users are solely responsible for ensuring that their multi-account usage complies with Google's Terms of Service and Anti-Abuse Policies.

---

## Documentation

- [MCP Server Setup & Tools](docs/mcp-configuration.md)
- [Compatibility Report](docs/compatibility.md)
- [Security Review & Threat Model](docs/security-review.md)
- [Milestone Implementation Plans](docs/plans/v1/README.md)
