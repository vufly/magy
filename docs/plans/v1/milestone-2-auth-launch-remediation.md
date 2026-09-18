# M2 Follow-up: Global Install And Interactive Auth Launch

## Status

**Approved plan; not implemented.** This document is an implementation handoff
for another harness. No source code or tests were changed as part of this
planning session.

## Goal

Make `magy profile auth NAME` and interactive passthrough launch the intended
official Agy executable without tool-manager side effects and with unchanged
terminal ownership.

This plan addresses the manual harness findings in:

[`../../verification/m2-auth-launch-harness.md`](../../verification/m2-auth-launch-harness.md)

It supplements, but does not replace, the broader M2 remediation list in
`docs/verification/m2-review.md`.

## Required Outcomes

1. Discovery preserves each candidate's original path for diagnostics and never
   turns a rejected shim into its target binary. Managed launch executes only a
   validated direct Agy path.
2. Managed profile launches use a direct Agy executable, not an indirect
   launcher whose behavior depends on the user's home or tool-manager state.
3. Tool-manager users can configure a generic lookup-only resolver command that
   returns the current direct Agy path on every launch, avoiding stale paths
   after upgrades or reshim operations.
4. Interactive Agy receives the caller's real stdin, stdout, and stderr file
   descriptors and sees the same TTY status as direct execution.
5. Redirected stdout remains owned by Agy and stays byte-for-byte scriptable.
6. Health updates use private Agy logs and exit status when streams are inherited
   rather than captured.

## Design Decisions

### Preserve invocation paths, not canonical targets

Executable paths have two identities:

- Invocation identity: the path and basename passed as `argv[0]`.
- Filesystem identity: the final target reached after symlink resolution.

Multicall tools such as mise dispatch from invocation identity. Discovery must
therefore retain the lexical candidate path instead of replacing it with a
resolved target. Storage and containment code should continue resolving paths;
this change applies only to executable discovery.

Doctor should distinguish rejected candidate path, configured resolver source,
and selected direct executable. It must not present the resolved multicall
target as Agy or execute a rejected candidate for version probing.

### Resolve a direct executable before profile isolation

No generic environment can satisfy both sides of an arbitrary launcher chain:
the launcher may need the real user home, while Agy must receive the synthetic
profile home. A child launched through a shim inherits one environment, so Magy
cannot transparently switch `HOME` after an unknown manager dispatches.

The generic safe contract is therefore:

- Discover and validate a direct Agy executable before constructing profile
  environment.
- Preserve lexical candidate paths for diagnostics; use canonical paths only to
  classify indirection, never as the invocation path.
- Enumerate PATH candidates rather than accepting only the first match. Skip
  symlink/multicall candidates and continue to a later direct `agy` executable.
- Reject indirect explicit `MAGY_AGY_CMD` or configured `agy_cmd` values with an
  actionable error rather than silently falling back.
- Add optional `agy_resolver` configuration as an argv array, executed without a
  shell under the original caller environment and working directory. It must
  print one direct executable path and exit successfully.
- Run the resolver on every doctor probe and launch. Do not persist its result;
  validate the returned path and use it only for that invocation. Tool-manager
  upgrades, version changes, and reshim operations therefore take effect on the
  next Magy command.
- If PATH contains only indirect launchers and no resolver is configured, doctor
  and launch fail without executing them. Tell the user to configure a direct
  path or lookup-only resolver.
- Do not add manager-specific `MISE_*`, `ASDF_*`, `PYENV_*`, Volta, or similar
  environment handling.

This intentionally trades transparent shim support for deterministic isolation
and no unexpected installations. The resolver protocol remains manager-neutral.
For the observed mise setup, configuration can use:

```json
{
  "agy_resolver": ["mise", "which", "agy"]
}
```

Equivalent lookup-only commands can support other managers without Magy knowing
their environment variables or filesystem layouts. Resolver commands are
trusted user configuration and must not install or mutate tools.

After selecting a direct executable, keep `HOME` profile-specific. On POSIX,
derive missing generic `XDG_CONFIG_HOME`, `XDG_DATA_HOME`, `XDG_CACHE_HOME`, and
`XDG_STATE_HOME` from the profile home so child state cannot fall back to real
user roots. Existing explicit XDG values require separate isolation review and
must not be silently repointed to the real home as a launcher workaround.

### Inherit terminal streams directly

For `capture_output=False`, launch the child without `stdin`, `stdout`, or
`stderr` redirection. This preserves TTY detection, terminal query/response
behavior, pipelines, redirection, broken-pipe semantics, and stdout
scriptability.

Do not add a PTY proxy. PTY proxying changes stream semantics, merges or rewrites
output on some platforms, and requires separate POSIX and Windows
implementations.

For `capture_output=True`, retain explicit capture for internal/test callers.
This mode is noninteractive by definition.

When `update_health=True`, inject a unique private Agy log unless the caller
already supplied one. In inherited-stream mode, classify from exit status and
bounded log tail. If no usable evidence exists, use conservative
`unknown-failure`; do not reclaim stdout/stderr by piping them.

## Implementation Steps

### M2-A1: Preserve executable candidate identity

Update `src/magy/agy.py`:

- Replace executable-path uses of `safe_expand_path()` and unconditional
  `Path.resolve()` with lexical absolute expansion plus separate indirection
  classification.
- Preserve explicit precedence while adding resolver support: direct
  `MAGY_AGY_CMD`, configured direct `agy_cmd` or configured `agy_resolver`, then
  direct `agy` candidates on `PATH`.
- Preserve explicit-source failure behavior; an invalid environment/config path
  must not silently fall back to another executable.
- Keep rejected shim paths for diagnostics but never return them as selected
  executables.
- Keep storage-path canonicalization unchanged.
- Make doctor version-probe only the selected direct executable.

### M2-A2: Enforce the direct-executable boundary

Update `src/magy/agy.py`, `src/magy/profiles.py`, and diagnostics as needed:

- Add candidate classification that distinguishes direct executable files from
  symlink/multicall indirection without executing candidates.
- Enumerate PATH entries and choose the first safe direct Agy candidate.
- Reject indirect explicit environment/config candidates source-specifically.
- Add `MagyConfig.agy_resolver` as an optional nonempty string argv list,
  mutually exclusive with static `agy_cmd`.
- Execute configured resolver without a shell, under bounded timeout and bounded
  output, using the original environment and caller working directory.
- Require one absolute executable path, reject indirect or invalid output, and
  never fall back silently after resolver failure.
- Resolve on every doctor/launch invocation; do not cache the direct path in
  config or registry.
- Return structured discovery failure explaining the rejected candidate and how
  to configure a direct path or resolver.
- Ensure doctor does not execute rejected candidates for version probing.
- Resolve the direct executable before creating the profile child environment.
- Keep profile `HOME`, `USERPROFILE`, `HOMEDRIVE`, and `HOMEPATH` behavior.
- Derive missing profile-local XDG roots on POSIX; do not point them to real
  user directories.

### M2-A3: Restore direct terminal ownership

Update `src/magy/profiles.py`:

- In non-capturing mode, start the child with inherited stdin/stdout/stderr.
- Remove pipe-forwarding threads and rolling stream buffers from that mode.
- Preserve child exit-code return and current signal-handler restoration while
  leaving broader process-tree remediation to the existing M2 review work.
- Make `update_health=True` imply private log injection when no caller log is
  supplied.
- Resolve relative caller log paths against child `cwd`, or the current working
  directory when `cwd` is absent.
- Classify inherited-mode runs from exit code and bounded log content only.

Update `src/magy/cli.py` only where needed to keep `profile auth`, `profile run`,
and top-level passthrough on the shared non-capturing launch path.

### M2-A4: Extend fake harness

Update `src/magy/testing/fake_agy.py` and `tests/conftest.py`:

- Add a generic fake multicall launcher whose `agy` symlink would perform a
  visible forbidden side effect if executed.
- Add a direct fake Agy later on PATH so fallback selection can be verified.
- Record invocation path and child isolation variables without credential data.
- Add an interactive probe that records `isatty(0/1/2)`, prints and flushes a
  prompt without newline, reads one response, and exits.
- Add log-only health evidence mode that writes to caller/injected `--log-file`.
- Ensure fake modes never perform network access or package installation.

## Test Plan

### Executable discovery

Add to `tests/test_discovery.py`:

- PATH discovery skips an indirect first candidate and selects a later direct
  Agy executable.
- An indirect-only PATH fails without invoking the launcher or creating its
  forbidden side effect.
- Indirect `MAGY_AGY_CMD` and configured `agy_cmd` values fail source-specifically
  without PATH fallback.
- Configured resolver returns a direct v1 fake Agy, then returns v2 after its
  manager-state fixture changes; the next Magy invocation uses v2 without config
  changes or persistent cache updates.
- Resolver failure, timeout, multiple output lines, relative paths, indirect
  output, and non-executable output fail without fallback.
- Doctor reports rejected invocation path and actionable direct-path guidance.
- Broken explicit symlinks fail source-specifically without PATH fallback.
- Broken PATH shims are ignored.

Add to `tests/test_doctor.py`:

- Doctor JSON/text reports the selected direct Agy path and version.
- Doctor never version-probes an indirect launcher.
- Indirect-only discovery fails without creating files or invoking launcher
  installation behavior.
- Doctor re-runs the resolver and reports a changed direct path after simulated
  manager upgrade/reshim.

### Environment isolation

Add to `tests/test_profiles.py`:

- Profile `HOME` remains synthetic and missing POSIX XDG roots become
  profile-local.
- Direct Agy executes while the rejected launcher remains uninvoked.
- No launcher/tool-manager directory or forbidden side-effect marker appears in
  the managed profile.
- Existing explicit XDG behavior is documented and covered without redirecting
  values to real user roots.
- Windows drive-letter and UNC profile-home behavior remains unchanged.

### Interactive terminal behavior

Add POSIX PTY integration tests in `tests/test_tty.py`:

- `profile auth` gives fake Agy TTY stdin/stdout/stderr.
- Prompt bytes become visible before test input is sent.
- Sending a response completes without hanging.
- Top-level explicit-profile passthrough preserves the same behavior.
- Tests use bounded polling/timeouts and guaranteed child cleanup.

Add portable subprocess tests:

- Redirected stdout contains only exact fake Agy output.
- Magy selected-profile diagnostics remain on stderr.
- `update_health=True` injects a private log for auth/run and classifies log-only
  evidence.
- Relative caller log paths are resolved against child `cwd`.

Use file-descriptor capture (`capfd`) where child output is inherited; Python
object-level `capsys` does not observe direct child file descriptors.

### Global uv installation smoke test

After unit tests pass, build and install Magy into temporary uv tool roots rather
than modifying the developer's real global installation:

```text
UV_TOOL_DIR=<temp>/tools
UV_TOOL_BIN_DIR=<temp>/bin
uv tool install <built-wheel>
```

Run the installed `magy doctor` and PTY auth smoke with an indirect launcher
first on PATH and a configured fake lookup resolver. Assert the indirect
launcher was never executed and no forbidden side-effect path appears under the
profile. Change the resolver fixture from fake Agy v1 to v2 and repeat without
reinstalling or reconfiguring Magy.

Perform one explicit local manual check with the real preinstalled Agy only after
automated gates pass. Do not inspect credential files; verify only that the
authentication TUI appears and accepts input. Configure the direct installed Agy
resolver for this smoke test, for example `mise which agy`; do not execute a
tool-manager shim under profile environment.

## Verification Commands

```text
uv run ruff check .
uv run ruff format --check .
uv run pytest tests/test_discovery.py tests/test_doctor.py tests/test_profiles.py tests/test_tty.py
uv run pytest
uv run --python 3.11 ruff check .
uv run --python 3.11 pytest
uv build
```

## Exit Criteria

- PATH discovery skips indirect launchers and selects a later direct Agy when
  available.
- Indirect-only PATH and explicit indirect configuration fail without executing
  the launcher or creating side effects.
- Generic resolver returns and validates the current direct Agy path on every
  invocation; simulated upgrade/reshim switches to a new binary without Magy
  reinstall or config edits.
- Doctor reports the selected direct Agy path and never probes rejected
  launchers.
- Profile launch creates no tool-manager installation tree under the managed
  profile.
- `magy profile auth NAME` shows its prompt before input and reports TTY on all
  three standard descriptors.
- Redirected/piped Agy stdout remains exact and Magy diagnostics remain on
  stderr.
- Health classification still works from private bounded logs without piping
  interactive streams.
- Temporary uv-tool installation smoke test passes.
- M2 review receives independent verification evidence before changing to Pass.

## Non-Goals

- Adding manager-specific resolver adapters or environment variables; only the
  generic configured resolver protocol is in scope.
- Transparently supporting arbitrary shims that require real-home state.
- Rewriting existing explicit generic XDG directory values without a separate
  isolation review.
- Implementing a PTY or ConPTY terminal emulator.
- Completing existing M2 lifecycle, removal, process-tree, redaction, routing,
  and settings-symlink remediations documented elsewhere.
- Reading, copying, or validating real credential contents.

## Implementation Handoff

The implementation harness should:

1. Read this plan, `docs/verification/m2-auth-launch-harness.md`, and the full
   unresolved finding list in `docs/verification/m2-review.md`.
2. Implement only the approved auth-launch scope unless explicitly assigned the
   remaining M2 findings.
3. Keep resolver configuration generic. Do not add hardcoded mise/asdf/pyenv or
   other manager adapters.
4. Run the resolver on every doctor/launch invocation and never persist its
   returned executable path, so upgrades and reshim changes are picked up.
5. Use only fake launchers/Agy for automated tests. Do not invoke official Agy
   or inspect credentials during automated verification.
6. Write implementation evidence to a new verification document; do not change
   this plan or the M2 review decision to Pass.
7. Request independent re-review after all focused and full verification gates
   pass.

Expected implementation report:

```text
docs/verification/m2-auth-launch-implementation.md
```
