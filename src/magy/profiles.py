import fnmatch
import io
import ntpath
import os
import shutil
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from magy.agy import resolve_agy_executable
from magy.config import get_data_dir, load_config_result
from magy.storage import (
    atomic_write_json,
    ensure_private_directory,
    ensure_private_file,
    read_json,
    safe_expand_path,
    update_json,
    validate_profile_name,
)

PROTECTED_ENV_VARS = frozenset({
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "MAGY_REAL_HOME",
    "AGY_CLI_DISABLE_AUTO_UPDATE",
    "MAGY_PROFILE",
})

ALLOWLISTED_SETTINGS_FILES = (
    "AGENTS.md",
    "settings.json",
    "trustedFolders.json",
    "antigravity-cli/settings.json",
    "antigravity-cli/keybindings.json",
)

ALLOWLISTED_SETTINGS_DIRS = (
    "commands",
    "config",
    "policies",
)

DENIED_FILE_PATTERNS = (
    "*token*",
    "*oauth*",
    "*credential*",
    "*secret*",
    "*account*",
    "*history*",
    "*conversation*",
    "*cache*",
    "*log*",
    "*trajectory*",
    "*trajectories*",
    "*install*",
    "*.db",
    "*.sqlite*",
    "*.sock",
)


@dataclass
class ProfileMetadata:
    name: str
    kind: str = "managed"  # "managed" | "external"
    home_dir: str | None = None
    enabled: bool = True
    created_at: float = field(default_factory=time.time)
    last_selected_at: float | None = None
    last_success_at: float | None = None
    last_failure_at: float | None = None
    health: str = "healthy"
    cooldown_until: float | None = None
    cooldown_reason: str | None = None

    def is_available(self, now: float | None = None) -> bool:
        if not self.enabled:
            return False
        if self.health == "disabled":
            return False
        if self.health == "healthy":
            return True
        if self.cooldown_until is not None:
            current = time.time() if now is None else now
            return current >= self.cooldown_until
        return False

    def resolved_home(self) -> Path:
        if self.kind == "managed" or self.home_dir is None:
            return get_profile_home_dir(self.name)
        return Path(self.home_dir)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ProfileMetadata":
        return cls(
            name=data["name"],
            kind=data.get("kind", "managed"),
            home_dir=data.get("home_dir"),
            enabled=data.get("enabled", True),
            created_at=data.get("created_at", 0.0),
            last_selected_at=data.get("last_selected_at"),
            last_success_at=data.get("last_success_at"),
            last_failure_at=data.get("last_failure_at"),
            health=data.get("health", "healthy"),
            cooldown_until=data.get("cooldown_until"),
            cooldown_reason=data.get("cooldown_reason"),
        )


def _check_no_symlink_and_contained(
    path: Path, expected_parent: Path, description: str
) -> None:
    """Verify that path is not a symlink and is contained in expected_parent."""
    if path.is_symlink() or os.path.islink(path):
        raise ValueError(f"{description} '{path}' cannot be a symlink")
    if path.exists():
        resolved = path.resolve()
        parent_resolved = expected_parent.resolve()
        is_contained = (
            resolved == parent_resolved
            or resolved.is_relative_to(parent_resolved)
        )
        if not is_contained:
            raise ValueError(
                f"{description} '{path}' resolves to '{resolved}', "
                f"which is outside '{parent_resolved}'"
            )


def get_profiles_dir() -> Path:
    """Return the managed profiles root directory with owner-only permissions."""
    data_dir = get_data_dir()
    path = data_dir / "profiles"
    _check_no_symlink_and_contained(path, data_dir, "Profiles root directory")
    ensure_private_directory(path)
    _check_no_symlink_and_contained(path, data_dir, "Profiles root directory")
    return path


def get_profile_dir(name: str) -> Path:
    """Return the directory for a specific profile."""
    validated = validate_profile_name(name)
    return get_profiles_dir() / validated


def get_profile_home_dir(name: str) -> Path:
    """Return the synthetic home directory for a profile."""
    return get_profile_dir(name) / "home"


def validate_profile_layout(name: str) -> None:
    """Validate that existing profile layout contains no symlink redirects."""
    profiles_root = get_profiles_dir().resolve()
    p_dir = get_profile_dir(name)
    _check_no_symlink_and_contained(p_dir, profiles_root, "Profile directory")

    p_home = p_dir / "home"
    _check_no_symlink_and_contained(p_home, p_dir, "Profile home directory")

    gemini_dir = p_home / ".gemini"
    _check_no_symlink_and_contained(gemini_dir, p_home, "Profile .gemini directory")

    cli_dir = gemini_dir / "antigravity-cli"
    _check_no_symlink_and_contained(cli_dir, gemini_dir, "Credential directory")


def get_registry_file_path() -> Path:
    """Return path to profiles.json in magy data directory."""
    return get_data_dir() / "profiles.json"


def load_profiles() -> dict[str, ProfileMetadata]:
    """Load all registered profiles."""
    path = get_registry_file_path()
    data = read_json(path, lock=True, default={})
    profiles_dict = data.get("profiles", {})
    return {
        name: ProfileMetadata.from_dict(p_data)
        for name, p_data in profiles_dict.items()
    }


def save_profiles(profiles: dict[str, ProfileMetadata]) -> None:
    """Save all profiles to registry atomically."""
    path = get_registry_file_path()
    data = {
        "version": 1,
        "profiles": {name: p.to_dict() for name, p in sorted(profiles.items())},
    }
    atomic_write_json(path, data, lock=True)


def get_profile(name: str) -> ProfileMetadata | None:
    """Look up a profile by name."""
    validated = validate_profile_name(name)
    profiles = load_profiles()
    return profiles.get(validated)


def _ensure_profile_registered(name: str) -> None:
    """Ensure that a managed profile is recorded in registry."""
    path = get_registry_file_path()

    def _update(data: Any) -> Any:
        if not isinstance(data, dict):
            data = {"version": 1, "profiles": {}}
        profiles = data.setdefault("profiles", {})
        if name not in profiles:
            p = ProfileMetadata(
                name=name,
                kind="managed",
                home_dir=str(get_profile_home_dir(name)),
            )
            profiles[name] = p.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})


def add_profile(
    name: str,
    kind: str = "managed",
    home_dir: Path | str | None = None,
) -> ProfileMetadata:
    """Register a new profile (managed or external)."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    if kind == "managed":
        ensure_profile_layout(validated)
        actual_home = str(get_profile_home_dir(validated))
    elif kind == "external":
        if home_dir is None:
            actual_home = str(Path.home())
        else:
            actual_home = str(safe_expand_path(home_dir))
        if not Path(actual_home).exists():
            raise FileNotFoundError(
                f"External home directory does not exist: {actual_home}"
            )
    else:
        raise ValueError(f"Invalid profile kind: {kind}")

    def _update(data: Any) -> Any:
        if not isinstance(data, dict):
            data = {"version": 1, "profiles": {}}
        profiles = data.setdefault("profiles", {})
        if validated in profiles and profiles[validated].get("kind") != kind:
            existing_kind = profiles[validated].get("kind")
            raise ValueError(
                f"Profile '{validated}' already exists with kind '{existing_kind}'"
            )
        meta = ProfileMetadata(
            name=validated,
            kind=kind,
            home_dir=actual_home,
        )
        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def remove_profile(name: str, force: bool = False) -> None:
    """Remove a profile from registry and delete managed storage."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()
    p_to_delete: ProfileMetadata | None = None

    def _update(data: Any) -> Any:
        nonlocal p_to_delete
        if not isinstance(data, dict):
            raise KeyError(f"Profile '{validated}' does not exist")
        profiles = data.get("profiles", {})
        if validated not in profiles:
            raise KeyError(f"Profile '{validated}' does not exist")
        p_to_delete = ProfileMetadata.from_dict(profiles.pop(validated))
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})

    # If managed, remove filesystem directory tree
    if p_to_delete and p_to_delete.kind == "managed":
        p_dir = get_profile_dir(validated)
        if p_dir.exists():
            shutil.rmtree(p_dir)


def enable_profile(name: str) -> ProfileMetadata:
    """Enable a profile."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    def _update(data: Any) -> Any:
        profiles = data.setdefault("profiles", {})
        if validated not in profiles:
            raise KeyError(f"Profile '{validated}' does not exist")
        meta = ProfileMetadata.from_dict(profiles[validated])
        meta.enabled = True
        if meta.health == "disabled":
            meta.health = "healthy"
        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def disable_profile(name: str) -> ProfileMetadata:
    """Disable a profile."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    def _update(data: Any) -> Any:
        profiles = data.setdefault("profiles", {})
        if validated not in profiles:
            raise KeyError(f"Profile '{validated}' does not exist")
        meta = ProfileMetadata.from_dict(profiles[validated])
        meta.enabled = False
        meta.health = "disabled"
        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def reset_profile_health(name: str) -> ProfileMetadata:
    """Reset profile health to healthy and clear cooldown."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    def _update(data: Any) -> Any:
        profiles = data.setdefault("profiles", {})
        if validated not in profiles:
            raise KeyError(f"Profile '{validated}' does not exist")
        meta = ProfileMetadata.from_dict(profiles[validated])
        meta.health = "healthy"
        meta.cooldown_until = None
        meta.cooldown_reason = None
        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def update_profile_health(
    name: str,
    health: str,
    cooldown_seconds: float | None = None,
    reason: str | None = None,
    is_success: bool = False,
) -> ProfileMetadata:
    """Update profile health and cooldown state in registry."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    def _update(data: Any) -> Any:
        profiles = data.setdefault("profiles", {})
        if validated not in profiles:
            meta = ProfileMetadata(
                name=validated,
                kind="managed",
                home_dir=str(get_profile_home_dir(validated)),
            )
        else:
            meta = ProfileMetadata.from_dict(profiles[validated])

        now = time.time()
        meta.health = health
        if is_success:
            meta.last_success_at = now
            meta.cooldown_until = None
            meta.cooldown_reason = None
        else:
            meta.last_failure_at = now
            if cooldown_seconds is not None:
                meta.cooldown_until = now + cooldown_seconds
            else:
                meta.cooldown_until = None
            meta.cooldown_reason = reason[:256] if reason else None

        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def sync_profile_settings(
    name: str, real_gemini_dir: Path | None = None
) -> list[Path]:
    """Synchronize safe non-auth configuration from real .gemini tree to profile.

    Copies only allowlisted settings, rejecting any path escaping .gemini.
    Explicitly excludes authentication tokens, histories, caches, and logs.
    """
    profile = get_profile(name)
    if profile and profile.kind == "external":
        return []

    p_home = get_profile_home_dir(name)
    target_gemini = p_home / ".gemini"
    ensure_private_directory(target_gemini)

    if real_gemini_dir is None:
        env_gemini = os.environ.get("MAGY_REAL_GEMINI")
        if env_gemini:
            real_gemini_dir = Path(env_gemini)
        elif os.environ.get("MAGY_REAL_HOME"):
            real_gemini_dir = Path(os.environ["MAGY_REAL_HOME"]) / ".gemini"
        else:
            real_gemini_dir = Path.home() / ".gemini"

    if not real_gemini_dir.exists():
        return []

    try:
        real_gemini_resolved = real_gemini_dir.resolve()
    except OSError:
        return []

    copied_files: list[Path] = []

    def _is_denied(filename: str) -> bool:
        low = filename.lower()
        return any(fnmatch.fnmatch(low, pat) for pat in DENIED_FILE_PATTERNS)

    def _safe_copy_file(src: Path, dest: Path) -> None:
        if _is_denied(src.name):
            return
        # Ensure symlinks do not escape real_gemini_resolved
        if src.is_symlink() or os.path.islink(src):
            try:
                resolved_src = src.resolve()
            except OSError:
                return
            if not resolved_src.is_relative_to(real_gemini_resolved):
                return
            if _is_denied(resolved_src.name):
                return

        ensure_private_directory(dest.parent)
        try:
            dest_resolved = dest.resolve()
            target_gemini_resolved = target_gemini.resolve()
            if not (
                dest_resolved == target_gemini_resolved
                or dest_resolved.is_relative_to(target_gemini_resolved)
            ):
                raise ValueError(
                    f"Destination path '{dest}' escapes target directory"
                )
        except OSError:
            pass

        content = src.read_bytes()
        dest.write_bytes(content)
        ensure_private_file(dest)
        copied_files.append(dest)

    # 1. Sync allowlisted files
    for rel_str in ALLOWLISTED_SETTINGS_FILES:
        src_path = real_gemini_dir / rel_str
        if src_path.exists() and src_path.is_file():
            dest_path = target_gemini / rel_str
            _safe_copy_file(src_path, dest_path)

    # 2. Sync allowlisted directories
    for rel_dir_str in ALLOWLISTED_SETTINGS_DIRS:
        src_dir = real_gemini_dir / rel_dir_str
        if src_dir.exists() and src_dir.is_dir():
            if src_dir.is_symlink() or os.path.islink(src_dir):
                try:
                    resolved_dir = src_dir.resolve()
                except OSError:
                    continue
                if not resolved_dir.is_relative_to(real_gemini_resolved):
                    continue

            for root, dirs, files in os.walk(src_dir):
                # Filter out symlinked dirs escaping real_gemini
                valid_dirs = []
                for d in dirs:
                    d_p = Path(root) / d
                    if d_p.is_symlink() or os.path.islink(d_p):
                        try:
                            if d_p.resolve().is_relative_to(real_gemini_resolved):
                                valid_dirs.append(d)
                        except OSError:
                            pass
                    else:
                        valid_dirs.append(d)
                dirs[:] = valid_dirs

                for f in files:
                    f_p = Path(root) / f
                    rel = f_p.relative_to(real_gemini_dir)
                    dest_f = target_gemini / rel
                    _safe_copy_file(f_p, dest_f)

    return copied_files


def ensure_profile_layout(name: str) -> tuple[Path, Path]:
    """Ensure directory structure exists with private permissions for a profile.

    Creates:
      <magy-data>/profiles/<name>
      <magy-data>/profiles/<name>/home
      <magy-data>/profiles/<name>/home/.gemini

    Fails closed if any path component is a symlink or resolves outside managed storage.
    """
    profiles_root = get_profiles_dir().resolve()

    p_dir = get_profile_dir(name)
    _check_no_symlink_and_contained(p_dir, profiles_root, "Profile directory")
    ensure_private_directory(p_dir)
    _check_no_symlink_and_contained(p_dir, profiles_root, "Profile directory")

    p_home = p_dir / "home"
    _check_no_symlink_and_contained(p_home, p_dir, "Profile home directory")
    ensure_private_directory(p_home)
    _check_no_symlink_and_contained(p_home, p_dir, "Profile home directory")

    gemini_dir = p_home / ".gemini"
    _check_no_symlink_and_contained(gemini_dir, p_home, "Profile .gemini directory")
    ensure_private_directory(gemini_dir)
    _check_no_symlink_and_contained(gemini_dir, p_home, "Profile .gemini directory")

    validate_profile_layout(name)
    _ensure_profile_registered(name)
    return p_dir, p_home


def apply_home_to_env(
    home_str: str,
    env: dict[str, str],
    os_type: str,
) -> None:
    """Populate home directory environment variables according to OS semantics."""
    if os_type == "nt":
        env["HOME"] = home_str
        env["USERPROFILE"] = home_str
        drive, path_without_drive = ntpath.splitdrive(home_str)
        env["HOMEDRIVE"] = drive
        env["HOMEPATH"] = path_without_drive
    else:
        env["HOME"] = home_str


def build_profile_env(
    name: str,
    base_env: dict[str, str] | None = None,
    os_name: str | None = None,
    home_override: str | None = None,
) -> dict[str, str]:
    """Build the isolated child process environment for a profile."""
    validate_profile_name(name)
    if home_override is None:
        profile = get_profile(name)
        if profile and profile.kind == "external" and profile.home_dir:
            p_home_str = str(Path(profile.home_dir).resolve())
        else:
            _, p_home = ensure_profile_layout(name)
            p_home_str = str(p_home.resolve() if p_home.is_absolute() else p_home)
    else:
        p_home_str = home_override

    env = dict(os.environ if base_env is None else base_env)
    os_type = os.name if os_name is None else os_name

    # Preserve real home for child diagnostics or reference
    if "MAGY_REAL_HOME" not in env:
        if os_type == "nt":
            orig_home = env.get("USERPROFILE") or env.get("HOME") or ""
        else:
            orig_home = env.get("HOME", "")
        env["MAGY_REAL_HOME"] = orig_home

    env["AGY_CLI_DISABLE_AUTO_UPDATE"] = "true"
    env["MAGY_PROFILE"] = name

    apply_home_to_env(p_home_str, env, os_type)
    return env


def run_in_profile(
    name: str,
    args: list[str],
    *,
    executable: Path | None = None,
    cwd: Path | None = None,
    env_overrides: dict[str, str] | None = None,
    capture_output: bool = False,
    timeout: float | None = None,
    sync_settings: bool = False,
    inject_log_file: bool = False,
    update_health: bool = False,
) -> Any:
    """Run an Agy command within the profile's isolated environment."""
    profile = get_profile(name)
    if profile is None or profile.kind == "managed":
        validate_profile_layout(name)
        if sync_settings:
            sync_profile_settings(name)

    if executable is None:
        cfg_res = load_config_result()
        if cfg_res.error:
            raise ValueError(f"Invalid configuration: {cfg_res.error}")
        exe, source = resolve_agy_executable(configured_cmd=cfg_res.config.agy_cmd)
        if exe is None:
            if source:
                raise FileNotFoundError(f"Agy executable invalid: {source}")
            raise FileNotFoundError("Agy executable not found")
        executable = exe

    if env_overrides:
        forbidden = set(env_overrides.keys()) & PROTECTED_ENV_VARS
        if forbidden:
            raise ValueError(
                f"Cannot override protected profile environment variables: "
                f"{', '.join(sorted(forbidden))}"
            )

    env = build_profile_env(name)
    if env_overrides:
        env.update(env_overrides)

    injected_log_file: Path | None = None
    cmd_args = list(args)
    if inject_log_file:
        has_log_arg = any(
            arg in ("--log-file", "-l") or arg.startswith("--log-file=")
            for arg in args
        )
        if not has_log_arg:
            injected_log_file = get_profile_dir(name) / "last_run.log"
            cmd_args = [*args, "--log-file", str(injected_log_file)]

    cmd = [str(executable), *cmd_args]

    if capture_output:
        res = subprocess.run(
            cmd,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        retcode = res.returncode
        stdout_text = res.stdout
        stderr_text = res.stderr
    else:
        # Stream stderr while capturing bounded recent content for health inspection
        captured_stderr = bytearray()
        stdin_target = None
        try:
            if sys.stdin and hasattr(sys.stdin, "fileno"):
                sys.stdin.fileno()
                stdin_target = sys.stdin
        except (io.UnsupportedOperation, AttributeError, OSError):
            stdin_target = None

        stdout_target = None
        try:
            if sys.stdout and hasattr(sys.stdout, "fileno"):
                sys.stdout.fileno()
                stdout_target = sys.stdout
        except (io.UnsupportedOperation, AttributeError, OSError):
            stdout_target = None

        proc = subprocess.Popen(
            cmd,
            env=env,
            cwd=cwd,
            stdin=stdin_target,
            stdout=stdout_target,
            stderr=subprocess.PIPE,
        )

        def _forward_stderr(pipe: Any) -> None:
            try:
                while True:
                    chunk = pipe.read(4096)
                    if not chunk:
                        break
                    try:
                        if hasattr(sys.stderr, "buffer"):
                            sys.stderr.buffer.write(chunk)
                            sys.stderr.buffer.flush()
                        else:
                            sys.stderr.write(chunk.decode(errors="replace"))
                            sys.stderr.flush()
                    except Exception:
                        pass
                    if len(captured_stderr) < 65536:
                        captured_stderr.extend(chunk[: 65536 - len(captured_stderr)])
            finally:
                pipe.close()

        t = threading.Thread(target=_forward_stderr, args=(proc.stderr,))
        t.daemon = True
        t.start()

        # Signal forwarding
        def _handler(signum: int, frame: Any) -> None:
            try:
                proc.send_signal(signum)
            except OSError:
                pass

        try:
            old_sigint = signal.signal(signal.SIGINT, _handler)
        except (ValueError, AttributeError):
            old_sigint = None
        try:
            old_sigterm = signal.signal(signal.SIGTERM, _handler)
        except (ValueError, AttributeError):
            old_sigterm = None

        try:
            retcode = proc.wait(timeout=timeout)
        finally:
            if old_sigint is not None:
                signal.signal(signal.SIGINT, old_sigint)
            if old_sigterm is not None:
                signal.signal(signal.SIGTERM, old_sigterm)
            t.join(timeout=1.0)

        stdout_text = ""
        stderr_text = captured_stderr.decode("utf-8", errors="replace")

    if update_health:
        from magy.agy import classify_run_health

        log_content = ""
        if injected_log_file and injected_log_file.exists():
            try:
                log_content = injected_log_file.read_text(encoding="utf-8")[-32768:]
            except OSError:
                pass

        classification = classify_run_health(
            retcode,
            stdout=stdout_text,
            stderr=stderr_text,
            log_content=log_content,
        )
        update_profile_health(
            name,
            classification.health,
            cooldown_seconds=classification.cooldown_seconds,
            reason=classification.reason,
            is_success=classification.is_success,
        )

    if capture_output:
        return res
    return retcode
