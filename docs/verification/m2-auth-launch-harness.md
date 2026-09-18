# M2 Global-Install Auth Launch Harness Findings

## Scope

This report records a manual failure observed after installing Magy globally
with `uv` and running:

```text
magy profile auth <profile-name>
```

The run used the locally installed official Agy command discovered through the
user's `PATH`. No credential contents were inspected.

## Result

**Fail.** The command did not launch a usable Agy authentication TUI. Three
independent launch defects compounded into one visible hang.

## Finding 1: Executable canonicalization destroyed shim identity

`shutil.which("agy")` returned this invocation path:

```text
/home/vudinhn/.local/share/mise/shims/agy
```

That path is a mise multicall shim implemented as a symlink to:

```text
/home/vudinhn/.local/bin/mise
```

`resolve_agy_executable()` canonicalized the discovered path with
`Path.resolve()`. Magy therefore executed the target path with `argv[0]` named
`mise`, rather than executing the shim path with `argv[0]` named `agy`. Mise did
not dispatch to Agy.

Observed doctor output:

```text
Antigravity (Agy):
  Executable: /home/vudinhn/.local/bin/mise (via PATH)
  Version:    2026.9.8 linux-x64 (2026-09-14)
```

Impact:

- Magy launched mise's own CLI instead of Agy.
- Doctor reported mise as a healthy Agy installation.
- Any symlink-based multicall launcher can fail for the same reason.

## Finding 2: Synthetic HOME relocated mise state and installs

Magy correctly changed `HOME` to the managed profile home for Agy account
isolation. It did not pin mise's manager-specific directories to the original
user environment and did not disable mise's automatic installation behavior.

Mise therefore derived its defaults from the synthetic home and created state
under:

```text
<profile-home>/.local/share/mise/
```

The accidental mise invocation triggered installation of a large configured
toolchain, including Go, Java, Node.js, Python, Rust, Antigravity, Claude Code,
and Codex. The run downloaded or compiled tools for more than four minutes and
consumed multiple gigabytes inside the managed profile.

Impact:

- Authentication launch caused unexpected network and installation side
  effects.
- Tool-manager state polluted the profile intended only for isolated Agy state.
- Repeated profiles can duplicate large tool installations.
- A missing or unresolved shim can silently install software rather than fail
  with an actionable error.

Relevant mise behavior is documented at:

- <https://mise.jdx.dev/dev-tools/shims.html>
- <https://mise.jdx.dev/directories.html>
- <https://mise.jdx.dev/configuration/settings.html#auto-install>
- <https://mise.jdx.dev/configuration/settings.html#not-found-auto-install>

## Finding 3: M2 stream capture removed the interactive TTY

The non-capturing launch path still starts the child with:

```text
stdout=subprocess.PIPE
stderr=subprocess.PIPE
```

Agy authentication requires real terminal descriptors. Under Magy, Agy saw
non-TTY output streams, emitted terminal query/control sequences, and blocked
waiting for terminal responses that the pipe-forwarding layer could not
provide.

Observed output included sequences equivalent to:

```text
\x1b[?1049h\x1b[2J\x1b[?u
```

Impact:

- `magy profile auth` hangs instead of presenting the authentication TUI.
- Short prompts can remain hidden because forwarding uses buffered
  `read(4096)` calls.
- Top-level interactive passthrough has the same defect.

## Root-Cause Chain

1. Discovery found the correct `agy` shim.
2. Canonicalization changed the executable identity from `agy` to `mise`.
3. Synthetic `HOME` made mise use profile-local manager directories.
4. Mise auto-install behavior materialized the configured toolchain there.
5. Piped output prevented either mise or Agy from owning the user's terminal.

Fixing only one item is insufficient. The launch contract must preserve
candidate identity for diagnostics, reject indirect launchers before profile
isolation, execute a direct Agy binary, and preserve terminal descriptors. This
avoids encoding manager-specific environment behavior while failing before any
implicit installation can start.

## Follow-up

Implementation plan, approved but not implemented in this session:

[`../plans/v1/milestone-2-auth-launch-remediation.md`](../plans/v1/milestone-2-auth-launch-remediation.md)

The M2 gate remains failed until focused automated regressions and a manual
global-install authentication smoke test pass.

Next harness should create
`docs/verification/m2-auth-launch-implementation.md` with exact code changes,
test commands, temporary uv-tool smoke evidence, and known gaps, then request an
independent M2 re-review.
