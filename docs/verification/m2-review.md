# Milestone 2 Remediation Re-review

## Decision

**Fail pending further remediation.** User-confirmed live authentication shows that
the primary auth flow works in the user's current terminal environment, and all
committed automated checks pass. However, process-group handling can stop an
interactive child under normal POSIX job control, timeout cleanup can leave
descendants running after the lifecycle lease is released, and settings
synchronization now deliberately exposes an external symlink tree inside managed
profiles. Additional security and persisted-state defects remain.

Milestone 3 remains blocked.

## Findings

### H1: Interactive child is placed in a background process group

- **Severity:** High
- **Location:** `src/magy/profiles.py:999-1015`, `tests/test_tty.py:23-41`
- **Affected step:** M2-S6

Every POSIX child is launched with `process_group=0`, but Magy never transfers
terminal foreground ownership to that new process group. In a controlling terminal
with normal job control, the child can write its prompt and then receive `SIGTTIN`
when it reads authentication input.

The PTY tests use `pty.openpty()` without creating a controlling terminal or setting
its foreground process group, so they do not exercise this behavior. A focused
controlling-terminal reproduction reported:

```text
child_pgrp=<child> foreground_pgrp=<magy> stopped=True stop_signal=21
```

The user's successful authentication is valid evidence for that terminal setup, but
does not remove this deterministic failure in terminals that enforce foreground
process-group reads.

Required fix:

- Preserve Magy's process group for inherited interactive launches, or implement
  correct terminal foreground handoff and restoration.
- Keep a separate process-group/tree strategy for noninteractive timeout and
  cancellation paths.
- Add a PTY test with a controlling terminal and explicit foreground process group.

### H2: Timeout cleanup leaves surviving descendants

- **Severity:** High
- **Location:** `src/magy/profiles.py:859-891`, `src/magy/profiles.py:1059-1079`,
  `tests/test_profiles.py:557-591`
- **Affected steps:** M2-S5/M2-S6

`_kill_process_tree()` returns as soon as the direct child exits after `SIGTERM`.
Descendants in the same process group that ignore `SIGTERM` therefore never receive
`SIGKILL`. It also returns immediately when the direct child has already exited,
without checking whether its process group still contains members. The lifecycle
lease is then released while those descendants remain active.

A focused reproduction used a direct child that exits on `SIGTERM` and a descendant
that ignores it:

```text
direct_returncode=0 descendant_alive=True
```

The committed timeout test checks health and lease count but never records or checks
the descendant PID.

Required fix:

- Continue process-group cleanup through the grace period even after the direct
  child exits, then signal the group with `SIGKILL` if members can remain.
- Reap the direct child before releasing the lease.
- Assert descendant death in the timeout regression.

### H3: Skills synchronization bypasses the no-external-symlink boundary

- **Severity:** High
- **Location:** `src/magy/profiles.py:718-760`,
  `tests/test_settings_sync.py:244-271`,
  `docs/plans/v1/milestone-2-profiles-routing-cli.md:36-38`
- **Affected step:** M2-S2

The new skills behavior skips `config/skills` during safe traversal, then creates a
managed-profile symlink to that source path. Source existence checks follow
symlinks, and there is no containment or denied-pattern filtering for files below
the linked tree. The test explicitly requires a source symlink pointing outside
`.gemini` to remain reachable from the managed profile.

This contradicts the M2 requirement that directory synchronization must not follow
a source symlink outside `.gemini`, and gives Agy direct access to everything later
added below the external target.

Required fix:

- Copy validated skill files through the existing allowlist pipeline, or define and
  enforce a separate contained skills root and file policy.
- Reject external source symlinks and destination symlink escapes.
- If direct linking is intentional, update the security model and milestone plan
  explicitly rather than claiming no external symlink exposure.

### H4: Common provider-prefixed credential names are not redacted

- **Severity:** High
- **Location:** `src/magy/agy.py:462-483`, `src/magy/agy.py:620-628`,
  `src/magy/profiles.py:517-542`
- **Affected step:** M2-S4

Credential-key matching starts at a word boundary. An underscore before the matched
suffix is a word character, so names such as `OPENAI_API_KEY`,
`GOOGLE_ACCESS_TOKEN`, and `AWS_SECRET_ACCESS_KEY` pass through unchanged. Unknown
failure text can then be persisted as `cooldown_reason` and returned by profile and
status output.

Focused calls to `sanitize_reason()` confirmed all three forms remained unchanged.

Required fix:

- Match credential fields as complete separator-delimited identifiers, including
  provider prefixes.
- Prefer controlled failure categories over persisted provider text.
- Add leak assertions for provider-prefixed token, API key, secret key, password,
  and authorization fields.

### H5: Lifecycle leases are disabled on Windows

- **Severity:** High
- **Location:** `src/magy/profiles.py:14-17`, `src/magy/profiles.py:241-311`
- **Affected steps:** M2-S1/M2-S5/M2-S6

When `fcntl` is unavailable, shared and exclusive lease acquisition both proceed
without locking, and active-operation count always returns zero. Removal can delete
profile state during authentication or a run, and an old operation can update a
replacement profile's metadata. The lease regression is not skipped on Windows and
would not satisfy its assertions there.

Required fix:

- Implement an actual Windows interprocess lifecycle lock, or explicitly reject the
  unsafe lifecycle operations until one exists.
- Add native Windows launch/remove race coverage.

### M1: Existing M2 registries cannot remove profiles after the incarnation change

- **Severity:** Medium
- **Location:** `src/magy/profiles.py:115-123`, `src/magy/profiles.py:375-401`
- **Affected step:** M2-S1

Profiles persisted before commit `dc262fd` have no `incarnation_id`.
`ProfileMetadata.from_dict()` invents a new UUID without persisting it, while
removal compares that generated value with the raw registry entry's missing value.
Removal therefore always reports an incarnation mismatch for those profiles.

A focused legacy-registry reproduction failed with:

```text
ValueError: Profile 'legacy' was modified or recreated concurrently (incarnation mismatch)
```

Required fix:

- Migrate and persist missing incarnation IDs under the registry lock before using
  them for comparison.
- Add an upgrade/removal regression using a pre-remediation registry fixture.

### M2: Auth and run accept unregistered profile names

- **Severity:** Medium
- **Location:** `src/magy/cli.py:258-274`, `src/magy/profiles.py:923-931`
- **Affected step:** M2-S5

`profile auth` and `profile run` call `run_in_profile()` directly. A missing registry
entry is treated as a managed profile, settings synchronization creates its home,
and the command runs. Health updates are silently discarded because the profile is
not registered. The resulting credentials and state cannot be listed, routed, or
removed through profile commands.

A focused reproduction returned success with `registered=False` and
`home_exists=True`.

Required fix:

- Require a registered profile and verify its incarnation while holding the shared
  lifecycle lease before any layout creation or launch.
- Add CLI regressions for nonexistent auth and run names.

### M3: Settings copy remains vulnerable to path check/use races

- **Severity:** Medium
- **Location:** `src/magy/profiles.py:627-689`
- **Affected step:** M2-S2

Source and destination components are checked by pathname and later reopened by
pathname through `read_bytes()`, `os.open()`, and `os.replace()`. A concurrent path
replacement can redirect a supposedly safe source read or destination write after
validation. The per-profile sync lock does not prevent mutation of source paths.
The previous review explicitly required no-follow, directory-descriptor-relative
operations; those were not implemented.

Required fix:

- Open validated roots and descendants relative to directory descriptors with
  no-follow semantics where supported.
- Add race/failure tests that replace source and destination components between
  validation and I/O.

## Open Question

The skills symlink commit appears intentional, but it conflicts with the approved
M2 isolation plan. Decide whether managed profiles may access an external mutable
skills tree. If yes, revise the plan and define its trust and filtering boundary
before implementation is treated as compliant.

## Confirmed Improvements

- User confirmed real authentication succeeds in the current environment.
- Destination and source `.gemini` root symlinks are rejected in tested cases.
- Shared POSIX lifecycle leases close the original count-then-remove race.
- Removal has incarnation checks and registry-update rollback for new-format data.
- Inherited streams restore prompt visibility and direct TTY descriptors in the
  existing PTY harness.
- Resolver-based executable lookup and lexical shim handling avoid the reproduced
  mise bootstrap failure.
- Private health logs are injected for health-updating launches.
- Signal-derived exit codes are normalized.

## Verification

| Command or check | Result |
| --- | --- |
| `uv run pytest -q` | PASS, 200 tests on Linux/Python 3.14.7 |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS, 46 files formatted |
| Controlling-terminal foreground-group reproduction | FAIL, child stopped by `SIGTTIN` |
| Ignored-`SIGTERM` descendant reproduction | FAIL, descendant remained alive |
| Provider-prefixed credential redaction reproduction | FAIL, values remained unchanged |
| Pre-incarnation registry removal reproduction | FAIL, incarnation mismatch |
| Unregistered profile launch reproduction | FAIL, command ran and home was created |

Native macOS and Windows verification was not run. User-confirmed live auth was
performed outside this automated review and no credentials were inspected.

## Remediation Handoff

1. Fix terminal foreground process-group ownership without losing complete tree
   cancellation.
2. Make timeout cleanup kill surviving process-group members before lease release.
3. Resolve the skills symlink policy conflict and close remaining sync path races.
4. Extend redaction to provider-prefixed credential fields.
5. Add Windows lifecycle locking and persisted registry migration.
6. Reject unregistered auth/run profile names.
7. Request another independent M2 re-review before starting M3.
