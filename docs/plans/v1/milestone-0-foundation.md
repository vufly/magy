# M0: Foundation

## Goal

Create the smallest cross-platform package foundation needed to test profile
isolation safely. Do not implement routing or MCP orchestration yet.

## Prerequisites

- Python 3.11 or newer.
- `uv` available for local development.
- Official `agy` installed for optional local probes.

## Steps

### M0-S1: Package scaffold

Create `pyproject.toml` with a `src/` layout and console entry points for `magy`
and `magy-mcp`. Add only these initial runtime dependencies:

- `mcp`
- `filelock`
- `platformdirs`

Add pytest and Ruff development configuration. Support Linux, macOS, and
Windows in CI.

### M0-S2: Path and executable discovery

Implement OS-native config, data, and state roots through `platformdirs`.
Discover Agy in this order:

1. `MAGY_AGY_CMD`
2. Configured executable path
3. `agy` on `PATH`

Resolve the executable before changing any profile home variables. `magy doctor`
must report executable path, version, and missing prerequisites without reading
credential contents.

### M0-S3: Fake Agy harness

Create a fake executable used by tests. It must be able to:

- Record selected environment variables and arguments.
- Emit configurable stdout, stderr, log text, and exit codes.
- Sleep until cancelled.
- Spawn a child process for process-tree cancellation tests.
- Simulate auth, quota, rate-limit, timeout, and success messages.

Never invoke real Agy from automated tests.

### M0-S4: Private and atomic storage helpers

Provide locked, atomic JSON writes and owner-only permissions where supported.
Validate profile names and run IDs as safe single path components. Tests must
cover interrupted replacement and concurrent writers.

## Deliverables

- Installable package skeleton.
- `magy doctor` executable/path diagnostics.
- Fake Agy fixture.
- Linux, macOS, and Windows CI jobs.
- Storage helper tests.

## Exit Criteria

- `uv run pytest` and `uv run ruff check .` pass.
- Fake Agy demonstrates environment capture and cancellation on each CI OS.
- No production profile or routing behavior exists yet.

## Review Focus

- Dependencies remain minimal.
- Tests cannot accidentally discover and invoke real `agy`.
- Atomic writes never expose partially written state.
- Diagnostics do not print environment secrets or credential paths.
