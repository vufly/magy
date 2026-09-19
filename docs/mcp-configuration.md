# Magy MCP Server Configuration Guide

This document outlines how to configure `magy-mcp` for headless delegation in
coding assistants like Codex and OpenCode using stdio transport.

`magy-mcp` delegates tasks to `agy` across isolated profiles with automatic
round-robin routing, cooldown handling, and health signal classification.

## Prerequisites

Install `magy` and `magy-mcp` globally or within your environment:

```bash
uv tool install magy
# or within a repo checkout
uv tool install --force .
```

Verify that `magy-mcp` is available on your `PATH`:

```bash
magy-mcp --help
```

---

## 1. Codex Configuration

To add `magy` as a tool provider in Codex, add the following configuration to
your Codex configuration file (e.g. `~/.codex/config.json` or
`~/.config/codex/config.json`):

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

If `magy-mcp` is installed in a specific environment (e.g. via `uv`), specify
the absolute binary path:

```json
{
  "mcpServers": {
    "magy": {
      "command": "/home/user/.local/bin/magy-mcp",
      "args": []
    }
  }
}
```

---

## 2. OpenCode Configuration

For OpenCode, add `magy` to your tools or MCP servers block (e.g. in
`~/.config/opencode/config.json` or project-level `.opencode/mcp.json`):

```json
{
  "mcp": {
    "servers": {
      "magy": {
        "command": "magy-mcp",
        "args": [],
        "type": "stdio"
      }
    }
  }
}
```

---

## 3. Available Tools

Once configured, the following tools are available through MCP:

| Tool | Purpose | Key Parameters |
| --- | --- | --- |
| `magy_run_start` | Start a detached, asynchronous Agy run | `prompt` (required), `workspace`, `profile`, `timeout`, `auto_approval`, `idempotency_key` |
| `magy_run_wait` | Short-poll until a run completes or times out | `run_id`, `timeout` (default 20s, max 60s) |
| `magy_run_status` | Query safe run status | `run_id` |
| `magy_run_result` | Retrieve bounded output chunks with stable offsets | `run_id`, `offset` (default 0), `limit` (default 64 KiB) |
| `magy_run_cancel` | Terminate a running or queued process tree | `run_id` |
| `magy_profiles` | Query registered profiles, availability, and routing | None |

> [!NOTE]
> `magy_run_start` defaults to `auto_approval=True` (`--dangerously-skip-permissions`)
> to enable unattended headless delegation. Set `auto_approval=False` if tool
> actions require interactive approval.
