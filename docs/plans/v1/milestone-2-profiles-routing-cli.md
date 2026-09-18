# M2: Profiles, Routing, And CLI

## Goal

Build complete `magy` profile management and launcher behavior after M1 proves
account isolation.

## Steps

### M2-S1: Profile registry

Persist profile metadata separately from profile homes. Track:

- Name, kind, enabled state, and creation time.
- Last selected, successful, and failed timestamps.
- Health: healthy, auth-required, rate-limited, quota-exhausted, or disabled.
- Cooldown deadline and bounded redacted reason.

Support managed profiles and one or more explicitly registered current/external
homes. Registering current home records a path; it does not copy credentials.

### M2-S2: Safe settings synchronization

Before each managed launch, copy these paths from the real `.gemini` tree when
present:

- `AGENTS.md`
- `commands/`
- `config/`
- `policies/`
- `settings.json`
- `trustedFolders.json`
- `antigravity-cli/settings.json`
- `antigravity-cli/keybindings.json`

Reject paths escaping `.gemini`. Explicitly exclude OAuth/token/account files,
installation IDs, histories, conversations, caches, logs, and trajectories.
Directory synchronization must not follow a source symlink outside `.gemini`.

### M2-S3: Round-robin selector

Store a persistent cursor and update it under a short file lock.

- Explicit profile selection uses the requested enabled profile and warns about
  known cooldown state.
- Automatic selection skips disabled, auth-required, rate-limited, and
  quota-exhausted profiles until cooldown expiry.
- No per-profile concurrency limit exists.
- Concurrent calls advance the same cursor deterministically.
- If no profile is available, report each reason and earliest expiry.

### M2-S4: Health classification

Inject a private `--log-file` when the caller has not supplied one. After exit,
inspect bounded recent stdout, stderr, and log content. Classify the latest
relevant signal as success, auth-required, rate-limited, quota-exhausted,
timeout, or unknown failure.

Use provider retry timing when available. Otherwise apply configurable default
cooldowns. Successful use clears transient health.

Do not claim that an untested profile has available quota. Health represents
only current evidence.

### M2-S5: Profile commands

Implement:

```text
magy profile add NAME
magy profile add NAME --current
magy profile auth NAME
magy profile list [--json]
magy profile show NAME [--json]
magy profile enable NAME
magy profile disable NAME
magy profile reset-health NAME
magy profile remove NAME
magy status [--json]
magy doctor [--json]
```

Removal must reject active managed operations and require confirmation unless a
documented noninteractive force flag is provided.

### M2-S6: Agy passthrough

Implement:

```text
magy --profile work -- <agy arguments...>
magy -- <agy arguments...>
```

Preserve terminal streams and child exit code. Print selected profile to stderr
so stdout remains scriptable. Forward termination signals on POSIX and Windows.

Never replay a failed invocation. Update health so only a later invocation can
choose another profile.

## Deliverables

- Profile lifecycle commands.
- Safe settings synchronization.
- Shared round-robin and health registry.
- Transparent Agy passthrough.
- JSON output suitable for future MCP and automation use.

## Exit Criteria

- Repeated automatic launches follow exact round-robin order.
- Known unhealthy profiles are skipped until cooldown expiry.
- Parallel selectors do not corrupt or lose cursor updates.
- Parallel launches may use the same profile.
- Auth files are never copied by settings synchronization.
- A failed task is never automatically restarted.

## Review Focus

- Path traversal and symlink escape prevention.
- Lock scope stays short and excludes child process lifetime.
- Explicit profile semantics remain predictable.
- Health parsing uses bounded input and latest-signal precedence.
- stdout remains owned by Agy, not Magy diagnostics.
