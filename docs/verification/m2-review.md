# Milestone 2 Remediation Re-review

## Decision

**Fail pending further remediation.** Commit `4b0e7c9` fixes several original
findings and all committed checks pass, but independently reproduced security
and lifecycle defects remain. In particular, settings synchronization still
follows a symlinked destination root, removal is not atomic against launch or
re-add, timeout handling releases activity protection while the child remains
alive, and failure-reason redaction still leaks common credential forms.

A global `uv` installation auth harness also exposed a compound launch failure:
executable discovery resolved the `agy` mise shim to the `mise` target, the
synthetic profile home redirected mise's data/install roots and triggered a
large automatic toolchain installation, and piped streams prevented the
interactive TUI from receiving a real terminal. Evidence and focused plan:

- [`m2-auth-launch-harness.md`](m2-auth-launch-harness.md)
- [`../plans/v1/milestone-2-auth-launch-remediation.md`](../plans/v1/milestone-2-auth-launch-remediation.md)

The focused plan is approved for handoff but remains unimplemented. Its
requirements must not be treated as remediation evidence until a separate
implementation report and independent re-review exist.

Milestone 3 remains blocked.

## Findings

### H1: A symlinked destination `.gemini` root still permits external overwrite

- **Severity:** High
- **Location:** `src/magy/profiles.py:523-554`,
  `src/magy/profiles.py:565-635`
- **Affected step:** M2-S2

The remediation rejects symlinked files and directories below
`target_gemini`, but does not reject `target_gemini` itself. Calling
`ensure_private_directory(target_gemini)` follows that symlink, and
`target_gemini.resolve()` makes the external target the accepted containment
root. All later containment checks therefore succeed relative to the wrong
directory.

Independent reproduction replaced a managed profile's `.gemini` directory
with a symlink to an external directory containing `settings.json`. A settings
sync changed the external file from `TOKEN` to `SAFE`:

```text
victim: SAFE
target_is_symlink: True
```

This leaves the original destination-link vulnerability open at a higher path
component. The real source root is also resolved without rejecting it when the
root itself is a symlink.

Required fix:

- Reject source and destination roots that are symlinks before any `mkdir`,
  `chmod`, traversal, or write.
- Revalidate the complete managed layout while holding the synchronization
  lock.
- Use no-follow, directory-descriptor-relative operations where available to
  close check/use races rather than trusting resolved path strings.
- Create temporary files with mode `0o600` at open time, not after writing.
- Add source-root and destination-root symlink regressions.

### H2: Active-operation removal protection remains racy

- **Severity:** High
- **Location:** `src/magy/profiles.py:222-288`,
  `src/magy/profiles.py:336-384`, `src/magy/profiles.py:771-800`
- **Affected step:** M2-S5

`remove_profile()` counts per-run locks, then proceeds without holding a lock
that prevents a new operation from starting. `run_in_profile()` does not acquire
its activity lock until after profile lookup, layout validation, settings sync,
executable resolution, and environment construction. Removal can therefore
observe zero operations while a launch is already in progress or can start
between the count and directory rename.

Independent reproduction paused a launch during settings synchronization,
removed the profile, then resumed it. The child ran successfully and
`build_profile_env()` recreated the removed managed home while the profile
remained absent from the registry:

```text
run_result: [0]
registered: False
home_recreated: True
```

Per-run lock enumeration also has a creation race: removal can open and delete
a newly created lock file before its owner has acquired `flock`, after which the
owner runs while holding a lock on an unlinked inode.

Required fix:

- Replace count-then-act with one per-profile lifecycle gate: operations acquire
  a shared/read lease before touching profile state; removal acquires an
  exclusive/write lease non-blockingly and holds it through registry and
  filesystem changes.
- Acquire the operation lease before settings synchronization or any operation
  capable of recreating profile storage.
- Atomically verify that the registry entry still represents the same profile
  incarnation before launch or removal.
- Add process-level start/remove, lock-creation/remove, and remove/run tests.

### H3: Removal can delete a replacement profile or strand a registered profile

- **Severity:** High
- **Location:** `src/magy/profiles.py:291-384`
- **Affected steps:** M2-S1/M2-S5

Removal reads metadata outside the registry mutation lock and later pops only by
name. A delayed remover can act on a newly re-added profile with the same name,
deleting the replacement registry entry and directory. No incarnation ID or
`created_at` comparison protects against this ABA race.

The staged cleanup is not transactional in the other direction either. The
managed directory is renamed before the registry update. If that update fails,
the profile remains registered but its expected home is gone. Independent
failure injection produced:

```text
error: registry unavailable
profile_dir_exists: False
staged: ['.deleting_broken_<id>']
```

Required fix:

- Serialize remove and add under the lifecycle/registry transaction.
- Give each profile incarnation an immutable ID and verify it before deletion.
- Roll back the staged rename if registry mutation fails.
- Report cleanup failures instead of silently ignoring `rmtree` errors.
- Add concurrent remove/remove, remove/re-add, registry-failure, and
  filesystem-failure tests.

### H4: Streaming timeout releases protection while child remains alive

- **Severity:** High
- **Location:** `src/magy/profiles.py:845-918`
- **Affected steps:** M2-S5/M2-S6

When `proc.wait(timeout=timeout)` raises `TimeoutExpired`, the streaming branch
restores signal handlers and joins reader threads briefly, but never terminates
or reaps the child. Unwinding then exits `track_active_operation()` and removes
the run lock. A timed-out child can continue using profile state while removal
reports zero active operations.

Independent local reproduction with a 30-second child and a 0.2-second timeout
observed the child still alive while `get_active_operation_count()` returned
zero.

Required fix:

- On timeout, terminate the complete child process group/tree, escalate to kill
  after a bounded grace period, drain pipes, and reap every direct child before
  releasing the activity lease.
- Keep activity protection until process-tree cleanup completes.
- Add timeout cleanup, reaping, lock-lifetime, and descendant-process tests.

### H5: Redaction still persists common OAuth and Basic-auth secrets

- **Severity:** High
- **Location:** `src/magy/agy.py:264-308`,
  `src/magy/profiles.py:456-497`
- **Affected step:** M2-S4

`sanitize_reason()` handles the tested `token=` and Bearer forms but misses
common compound credential names and non-Bearer authorization schemes.
Independent reproductions returned:

```text
access_token=super-secret-value
Authorization: [REDACTED] dXNlcjpwYXNz
```

The first value is unchanged; the second leaves the Basic credential payload.
Similar forms such as `refresh_token`, `client_secret`, and `x-api-key` are not
covered consistently. These values can become durable `cooldown_reason`
metadata and appear in CLI/JSON output.

Required fix:

- Prefer controlled reason categories over persisting provider text.
- If text is retained, redact the complete value for any Authorization header,
  OAuth token/key/password field, JSON key/value form, URL credential, and
  common compound key spelling.
- Add positive leak assertions for Basic auth, `access_token`, `refresh_token`,
  `client_secret`, `apiKey`, `x-api-key`, quoted JSON, and URL values.

### H6: Piped stream forwarding breaks interactive prompts

- **Severity:** High
- **Location:** `src/magy/profiles.py:845-889`
- **Affected step:** M2-S6

Both child streams are piped and forwarded with buffered
`pipe.read(4096)`. Short flushed output can remain buffered until 4096 bytes or
EOF. A local child that printed `PROMPT>` and then slept showed no output after
300 ms; the prompt appeared only after the child exited about one second later.

This violates terminal-stream preservation and can deadlock interactive
authentication: Agy waits for input while the user cannot see its prompt.

Required fix:

- Forward output in a genuinely incremental manner, or preserve direct terminal
  ownership and capture bounded health evidence through a separate safe
  mechanism.
- Verify prompts become visible before child exit and stdin remains interactive.
- Add a Magy-mediated interactive prompt/response regression.

The global-install harness reproduced the production impact directly: Agy
emitted terminal query/control sequences and blocked because its output streams
were pipes rather than TTYs. This finding is covered by the focused auth-launch
remediation plan linked above.

### H7: Executable discovery destroys multicall shim identity

- **Severity:** High
- **Location:** `src/magy/agy.py:27-70`
- **Affected steps:** M0 executable discovery / M2-S5/M2-S6

`resolve_agy_executable()` canonicalizes direct and PATH-discovered executable
paths. The PATH result `/home/vudinhn/.local/share/mise/shims/agy` therefore
became `/home/vudinhn/.local/bin/mise`. Mise dispatches shims from `argv[0]`;
executing the target as `mise` runs the manager CLI instead of Agy. Doctor
reported mise's own version as if it were Agy.

Required fix:

- Make executable paths absolute without resolving symlinks.
- Preserve source precedence and explicit-source failure behavior.
- Report and execute the lexical invocation path.
- Add fake multicall shim coverage for discovery, doctor, and profile launch.

### H8: Indirect launchers can bootstrap tool-manager state inside profiles

- **Severity:** High
- **Location:** `src/magy/profiles.py:721-754`,
  `src/magy/agy.py:75-93`
- **Affected steps:** M1 isolation / M2-S5/M2-S6

The profile child inherits a synthetic `HOME`. Any indirect launcher that uses
home-relative configuration, data, or automatic installation will therefore
operate inside the managed profile before Agy starts. The global-install harness
demonstrated this through mise, triggering multi-gigabyte installation of a
configured toolchain under `<profile-home>/.local/share/mise/`, but the design
problem applies to any stateful tool-manager shim.

Required fix:

- Resolve and validate a direct Agy executable before profile isolation.
- Skip indirect PATH candidates and continue to a later direct executable.
- Reject indirect explicit configuration and indirect-only PATH discovery
  without executing the launcher.
- Support a generic configured resolver argv that runs under original
  environment and returns the current direct executable path on every command,
  so package-manager upgrades and reshim operations do not stale a stored path.
- Do not solve launcher state by pointing generic XDG state back to real home.
- Add generic forbidden-side-effect tests plus a temporary global uv-tool
  installation smoke test.

### M1: Termination forwarding is incomplete

- **Severity:** Medium
- **Location:** `src/magy/profiles.py:820-915`
- **Affected step:** M2-S6

Streaming mode sends signals only to the immediate child and does not create a
dedicated process group. Descendants can survive while the activity lock is
released. `capture_output=True` uses `subprocess.run()` and installs no forwarding
handler. Signal exits are returned as negative numbers; passing `-15` through
`sys.exit()` yields shell status 241 rather than conventional 143.

Required fix:

- Launch and terminate a dedicated process group/tree on supported platforms.
- Define equivalent capture and streaming cancellation semantics.
- Normalize signal-derived shell exit codes.
- Add SIGINT/SIGTERM process-tree, capture-mode, and shell-status tests.

Native Windows execution remains deferred, but current `fcntl is None` behavior
also reports zero active operations for every Windows run and must remain an
explicit backlog blocker rather than being described as resolved.

### M2: Health latest-signal and retry timing remain incorrect

- **Severity:** Medium
- **Location:** `src/magy/agy.py:328-422`
- **Affected step:** M2-S4

Concatenating stdout, stderr, and log content imposes source order, not temporal
order. Any log match is treated as newer than any stdout match regardless of
when each occurred. A newer stdout authentication signal and older log rate
signal classified as rate-limited.

When the winning category is rate limiting, `parse_retry_seconds(combined)`
returns the first retry value rather than the value associated with the latest
winning signal. This input:

```text
429 retry in 1 hour
429 retry in 30s
```

classified with a 3600-second cooldown instead of 30 seconds.

Required fix:

- Preserve event ordering while capturing streams, or define a conservative
  precedence rule that does not claim chronology across independent sources.
- Parse retry timing from the winning/latest matched signal and adjacent text.
- Add cross-source order and repeated-rate-signal tests.

### M3: Registry and routing mutations are not atomic with selection

- **Severity:** Medium
- **Location:** `src/magy/routing.py:47-120`,
  `src/magy/profiles.py:456-520`
- **Affected steps:** M2-S1/M2-S3

Selection snapshots registry state under the routing lock, commits the cursor,
then records selection under a separate registry lock. A concurrent disable or
health update can make the selected profile unavailable before return; removal
can instead produce `KeyError` after the cursor already advanced.

`last_selected_at` is assigned unconditionally from a timestamp captured before
the routing lock, so delayed concurrent selection can move the persisted value
backward.

Required fix:

- Define and implement atomic selection semantics across cursor and registry
  eligibility, with deterministic handling of concurrent lifecycle changes.
- Keep `last_selected_at` monotonic.
- Add selection/disable, selection/cooldown, selection/remove, and concurrent
  timestamp tests across processes.

### M4: Log injection and caller-log inspection are incomplete

- **Severity:** Medium
- **Location:** `src/magy/profiles.py:757-816`,
  `src/magy/profiles.py:920-940`, `src/magy/cli.py:258-265`
- **Affected step:** M2-S4

Unique log injection occurs only when `inject_log_file=True`. Top-level
passthrough enables it, but `magy profile auth` and `magy profile run` update
health without enabling log injection. The documentation claim that all profile
runs receive a unique private log is therefore false.

Relative caller-supplied log paths are interpreted relative to Magy's current
directory during inspection, not the child `cwd`, so health can ignore the log
the child actually wrote.

Required fix:

- Inject a unique private log for every health-updating launch unless the caller
  supplies one.
- Resolve relative caller log paths against child `cwd`.
- Add auth/run injection and relative-log-with-`cwd` tests.

### M5: Claimed concurrent process coverage is sequential

- **Severity:** Medium
- **Location:** `tests/test_routing.py:168-190`,
  `docs/verification/m2-implementation.md:45-53`,
  `docs/verification/m2-review.md`
- **Affected step:** M2-S3

`test_spawned_process_round_robin_distribution` invokes six blocking
`subprocess.run()` calls in a loop. It does not use `ProcessPoolExecutor`, does
not overlap processes, and does not test concurrent cursor advancement.
Several test names cited by the previous Pass report also do not exist.

Required fix:

- Start selectors concurrently across OS processes using a barrier and assert
  exact distribution, final cursor, and no lost updates.
- Keep implementation and review evidence aligned with actual test names and
  behavior.

## Confirmed Improvements

- Denied patterns are evaluated across nested relative path components.
- Leaf and intermediate destination symlinks below a valid destination root are
  rejected in the tested cases.
- In-tree source symlinks are skipped in the tested cases.
- Synchronized files use atomic replacement under a per-profile sync lock.
- Health updates no longer recreate missing registry entries.
- Duplicate profile add is rejected and new profiles use `untested` health.
- Disable/enable preserves tested cooldown state.
- Basic `last_selected_at` persistence works without competing mutations.
- Stdout is inspected for health signals and log reads are bounded from EOF.
- Top-level passthrough uses unique per-run log names.
- CLI version, malformed profile, equals syntax, and misplaced-subcommand cases
  are covered.
- Seconds, minutes, and hours are parsed for simple retry text.

## Verification

| Command / check | Result |
| --- | --- |
| `uv run ruff check .` | PASS |
| `uv run ruff format --check .` | PASS, 42 files formatted |
| `uv run pytest` | PASS, 168 tests on Python 3.14.7 |
| `uv run --python 3.11 ruff check .` | PASS |
| `uv run --python 3.11 pytest` | PASS, 168 tests on Python 3.11.16 |
| `uv build` | PASS, wheel and source distribution built |
| Destination-root symlink overwrite reproduction | FAIL as expected; external victim changed |
| Launch/remove race reproduction | FAIL as expected; removed home recreated and child ran |
| Registry-update failure during removal | FAIL as expected; registered profile lost expected home |
| OAuth/Basic redaction reproductions | FAIL as expected; credential material remained |
| Latest retry timing reproduction | FAIL as expected; 1 hour selected over newer 30 seconds |
| Interactive prompt forwarding reproduction | FAIL as expected; prompt delayed until child exit |
| Streaming timeout cleanup reproduction | FAIL as expected; child alive after activity count reached zero |

No official Agy invocation or real credential inspection was used.

## Remediation Handoff

1. Close destination-root/source-root symlink handling and add no-follow path
   tests.
2. Replace per-run lock counting with an atomic lifecycle lease shared by launch
   and removal; make removal transactional and incarnation-aware.
3. Terminate and reap complete process trees before releasing activity state;
   restore genuinely interactive stream forwarding.
4. Store controlled health reasons or comprehensively redact all credential
   forms; associate retry timing with the latest winning signal.
5. Make selection robust against concurrent registry mutation and make
   timestamps monotonic.
6. Add actual concurrent-process, signal, timeout, prompt, removal-race, and
   every-launch log tests.
7. Correct implementation evidence and request another independent re-review.
8. Do not begin M3 until this review passes.

Implement the focused global-install/auth-launch plan alongside these items; its
exit criteria are part of the M2 gate.
