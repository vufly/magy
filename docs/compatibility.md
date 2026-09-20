# Magy Compatibility Report

This document records the compatibility testing, supported platforms, runtime dependencies, and Antigravity (Agy) version interoperability for Magy.

---

## 1. Verified Platforms and Environments

### Operating Systems
- **Linux (x86_64, aarch64)**:
  - Verified on modern Linux kernels (6.x+), systemd and non-systemd environments.
  - Strict POSIX file permissions (`0o700` directories, `0o600` files) fully supported.
  - Process tree termination via `/proc` traversal and PGID signaling verified.
- **macOS (Darwin x86_64, arm64)**:
  - POSIX permissions and process group signaling verified.
  - Path and symlink security protections verified.
- **Windows (x86_64)**:
  - Reserved filename protections enforced (`CON`, `PRN`, `AUX`, `NUL`, `COM1-9`, `LPT1-9`).
  - Path separators and cross-platform path resolution verified.

### Python Versions
Magy is built and tested against:
- **Python 3.11** (Minimum supported runtime)
- **Python 3.12**
- **Python 3.13**
- **Python 3.14** (Development baseline)

---

## 2. Antigravity (Agy) Interoperability

Magy acts as a multi-profile orchestrator for the Antigravity CLI (`agy`). It isolates profiles by setting a synthetic `HOME` and `USERPROFILE` environment for each managed profile.

### Tested Agy Versions

| Agy Version Family | Compatibility Status | Profile Isolation Verification |
| :--- | :--- | :--- |
| **`1.2.x`** (e.g., `1.2.6`) | **Verified** | Verified with synthetic home and SQLite/file storage. |
| **`1.1.x`** | **Verified** | Verified with synthetic home. |
| **`1.0.x`** | **Verified** | Verified with synthetic home. |
| **`2.x.x` / Nightly** | **Unverified** | Unverified; subject to upstream storage and keyring changes. |

### Diagnostic Evaluation
`magy doctor` automatically inspects the active Agy binary and evaluates its version:
- If a verified version is detected (`1.0.x` through `1.2.x`), `magy doctor` reports:
  `Compatibility: verified (<version> tested with profile isolation)`
- If an unverified or unknown version is detected, `magy doctor` reports:
  `Compatibility: unverified (<version> not verified; undocumented storage or keyring changes may break profile isolation)`

`magy doctor` will **not** fail merely due to an unverified version, but warns the user that isolation guarantees have not been formally confirmed for that release.

---

## 3. Storage and Isolation Architecture

### Assumptions
1. **Home Directory Scoping**: Agy stores session credentials, tokens, SQLite cache, and user configuration under `$HOME/.gemini/` and `$HOME/.config/antigravity/` (or OS-equivalent user profile paths).
2. **Keyring Independence**: Current verified versions of Agy honor the isolated filesystem tree within `$HOME` for user secrets rather than sharing an unscoped system-wide keyring service without per-profile isolation.
3. **Executable Immutability**: Agy binaries resolved by Magy must be directly executable binaries or valid shell/wrapper scripts. Magy validates resolved executables and prohibits self-referential or circular invocations.

### Important Caveats
> [!WARNING]
> **Undocumented Upstream Changes**
> If a future upstream release of Antigravity switches credential storage to a centralized OS keyring (such as macOS Keychain or Windows Credential Manager) without namespacing items per home directory or user profile, synthetic home directory isolation alone may not separate login states.
> Always run `magy doctor` following upstream Agy updates.
