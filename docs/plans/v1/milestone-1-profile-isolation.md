# M1: Profile Isolation Gate

## Goal

Prove that generic home environment variables are sufficient to keep two Agy
accounts separate. This milestone is a hard gate for all later work.

## Proposed Launch Environment

For managed profiles, create `<magy-data>/profiles/<name>/home/.gemini`.

Linux and macOS:

```text
HOME=<profile-home>
MAGY_REAL_HOME=<original-home>
AGY_CLI_DISABLE_AUTO_UPDATE=true
```

Windows:

```text
HOME=<profile-home>
USERPROFILE=<profile-home>
HOMEDRIVE=<profile drive>
HOMEPATH=<profile path without drive>
MAGY_REAL_HOME=<original-home>
AGY_CLI_DISABLE_AUTO_UPDATE=true
```

Preserve `PATH`, current working directory, terminal streams, `SSH_AUTH_SOCK`,
and the rest of the inherited environment. Do not rewrite XDG variables unless
an observed Agy behavior requires it and the reason is documented.

## Steps

### M1-S1: Managed profile launcher

Implement an internal launcher that builds the environment above and runs the
already-resolved Agy executable. Preserve stdin, stdout, stderr, and exit code.
Add unit tests against fake Agy for every OS environment shape.

### M1-S2: Profile bootstrap

Add only enough temporary CLI surface to create and authenticate a named
profile. Authentication must run visibly. Do not copy existing auth files into
the managed home.

### M1-S3: First-profile live probe

Authenticate one managed profile. Verify:

- Agy writes its state beneath the managed home.
- A later launch reuses that authentication.
- Real `~/.gemini` files are not modified, except behavior initiated by Agy
  outside its documented state path that is explicitly recorded.

### M1-S4: Two-account live probe

Authenticate a second managed profile with a different account. Run `agy
models` and a minimal print-mode request through each profile. Capture only
redacted account evidence and result status. Never include token values in test
artifacts.

### M1-S5: Concurrent live probe

Start both profiles concurrently. Verify each continues using its intended
account and that one profile's logout, refresh, or cache activity does not alter
the other's effective identity.

### M1-S6: Record compatibility result

Write a short report under `docs/plans/v1/verification/` containing:

- Agy version and OS.
- Commands executed with secrets removed.
- Expected and observed identity separation.
- Filesystem paths created, without file contents.
- Keyring prompts or cross-profile behavior.
- Pass/fail decision.

## Stop Condition

Stop implementation if either profile silently uses the other profile's account
or native keyring state cannot be isolated by home variables. Do not add token
copying, file swapping, or global keyring mutation.

A failed result requires a new design for separate OS users, containers, or a
documented platform-specific keyring mechanism.

## Exit Criteria

- Two managed homes retain distinct accounts across repeated launches.
- Both can run concurrently.
- Real-home authentication remains unchanged.
- Live result is documented and explicitly marked passed.

## Review Focus

- No credential files are read by Magy.
- No tests or logs expose account tokens.
- Environment changes are process-local.
- Failure path stops later milestones rather than silently degrading isolation.
