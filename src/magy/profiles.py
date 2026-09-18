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
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

try:
    import fcntl
except ImportError:
    fcntl = None  # type: ignore

from magy.agy import resolve_agy_executable
from magy.config import get_data_dir, get_state_dir, load_config_result
from magy.storage import (
    atomic_write_json,
    ensure_private_directory,
    ensure_private_file,
    get_lock,
    read_json,
    safe_expand_path,
    update_json,
    validate_profile_name,
)

PROTECTED_ENV_VARS = frozenset(
    {
        "HOME",
        "USERPROFILE",
        "HOMEDRIVE",
        "HOMEPATH",
        "MAGY_REAL_HOME",
        "AGY_CLI_DISABLE_AUTO_UPDATE",
        "MAGY_PROFILE",
    }
)

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
    health: str = "untested"
    cooldown_until: float | None = None
    cooldown_reason: str | None = None
    pre_disable_health: str | None = None

    def is_available(self, now: float | None = None) -> bool:
        if not self.enabled:
            return False
        if self.health == "disabled":
            return False
        if self.health in ("healthy", "untested"):
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
            health=data.get("health", "untested"),
            cooldown_until=data.get("cooldown_until"),
            cooldown_reason=data.get("cooldown_reason"),
            pre_disable_health=data.get("pre_disable_health"),
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
        is_contained = resolved == parent_resolved or resolved.is_relative_to(
            parent_resolved
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


@contextmanager
def track_active_operation(name: str):
    """Track an active operation on a profile using a non-blocking lock."""
    validated = validate_profile_name(name)
    run_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    run_dir = get_state_dir() / "runs" / validated
    run_dir.mkdir(parents=True, exist_ok=True)
    ensure_private_directory(run_dir)
    lock_file = run_dir / f"{run_id}.lock"

    fd = os.open(str(lock_file), os.O_RDWR | os.O_CREAT, 0o600)
    if fcntl is not None:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as e:
            os.close(fd)
            raise RuntimeError(
                f"Could not acquire run lock for '{validated}': {e}"
            ) from e

    try:
        yield run_id
    finally:
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        try:
            os.close(fd)
        except OSError:
            pass
        try:
            lock_file.unlink(missing_ok=True)
        except OSError:
            pass


def get_active_operation_count(name: str) -> int:
    """Return count of currently active operations for a profile."""
    validated = validate_profile_name(name)
    run_dir = get_state_dir() / "runs" / validated
    if not run_dir.is_dir():
        return 0
    active = 0
    for lock_file in list(run_dir.glob("*.lock")):
        try:
            fd = os.open(str(lock_file), os.O_RDWR)
        except OSError:
            continue
        if fcntl is not None:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                # Lock acquired: previous process exited without cleaning up stale lock
                fcntl.flock(fd, fcntl.LOCK_UN)
                os.close(fd)
                try:
                    lock_file.unlink(missing_ok=True)
                except OSError:
                    pass
            except (BlockingIOError, OSError):
                # Lock is currently held by an active operation
                os.close(fd)
                active += 1
        else:
            os.close(fd)
    return active


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
        if validated in profiles:
            raise ValueError(f"Profile '{validated}' already exists")
        meta = ProfileMetadata(
            name=validated,
            kind=kind,
            home_dir=actual_home,
            health="untested",
        )
        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def remove_profile(name: str, force: bool = False) -> None:
    """Remove a profile from registry and delete managed storage.

    Rejects removal if active operations are running on the profile.
    The force parameter bypasses CLI confirmation, NOT active-operation safety.
    """
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    existing = get_profile(validated)
    if existing is None:
        raise KeyError(f"Profile '{validated}' does not exist")

    active_count = get_active_operation_count(validated)
    if active_count > 0:
        raise RuntimeError(
            f"Cannot remove profile '{validated}': active operations are running"
        )

    # Stage directory deletion for managed profiles
    deleting_dir: Path | None = None
    if existing.kind == "managed":
        p_dir = get_profile_dir(validated)
        if p_dir.exists():
            stage_name = f".deleting_{validated}_{uuid.uuid4().hex[:8]}"
            deleting_dir = p_dir.parent / stage_name
            try:
                p_dir.rename(deleting_dir)
            except OSError as e:
                raise OSError(
                    f"Failed to stage profile directory for removal: {e}"
                ) from e

    def _update(data: Any) -> Any:
        if isinstance(data, dict):
            profiles = data.get("profiles", {})
            profiles.pop(validated, None)
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})

    # Clean up staged managed directory
    if deleting_dir and deleting_dir.exists():
        shutil.rmtree(deleting_dir, ignore_errors=True)

    # Clean up runs directory
    run_dir = get_state_dir() / "runs" / validated
    if run_dir.exists():
        shutil.rmtree(run_dir, ignore_errors=True)


def enable_profile(name: str) -> ProfileMetadata:
    """Enable profile, restoring health without clearing active cooldown."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    def _update(data: Any) -> Any:
        profiles = data.setdefault("profiles", {})
        if validated not in profiles:
            raise KeyError(f"Profile '{validated}' does not exist")
        meta = ProfileMetadata.from_dict(profiles[validated])
        meta.enabled = True
        if meta.health == "disabled":
            meta.health = meta.pre_disable_health or "untested"
        meta.pre_disable_health = None
        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def disable_profile(name: str) -> ProfileMetadata:
    """Disable a profile, preserving its health and cooldown state."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    def _update(data: Any) -> Any:
        profiles = data.setdefault("profiles", {})
        if validated not in profiles:
            raise KeyError(f"Profile '{validated}' does not exist")
        meta = ProfileMetadata.from_dict(profiles[validated])
        meta.enabled = False
        if meta.health != "disabled":
            meta.pre_disable_health = meta.health
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
        meta.pre_disable_health = None
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
) -> ProfileMetadata | None:
    """Update profile health and cooldown state in registry.

    Fails silently (returning None) if profile does not exist in registry,
    preventing resurrection of removed profiles.
    """
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    from magy.agy import sanitize_reason

    def _update(data: Any) -> Any:
        profiles = data.get("profiles", {})
        if validated not in profiles:
            return data

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
            meta.cooldown_reason = sanitize_reason(reason) if reason else None

        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    return get_profile(validated)


def record_profile_selection(
    name: str, selected_at: float | None = None
) -> ProfileMetadata:
    """Record that a profile was selected for execution."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()
    now = time.time() if selected_at is None else selected_at

    def _update(data: Any) -> Any:
        profiles = data.setdefault("profiles", {})
        if validated not in profiles:
            raise KeyError(f"Profile '{validated}' does not exist")
        meta = ProfileMetadata.from_dict(profiles[validated])
        meta.last_selected_at = now
        profiles[validated] = meta.to_dict()
        return data

    update_json(path, _update, default={"version": 1, "profiles": {}})
    res = get_profile(validated)
    assert res is not None
    return res


def sync_profile_settings(name: str, real_gemini_dir: Path | None = None) -> list[Path]:
    """Synchronize safe non-auth configuration from real .gemini tree to profile.

    Copies only allowlisted settings, rejecting any path escaping .gemini.
    Explicitly excludes authentication tokens, histories, caches, and logs.
    """
    profile = get_profile(name)
    if profile and profile.kind == "external":
        return []

    p_dir = get_profile_dir(name)
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
        real_gemini_dir.resolve()
        target_gemini_resolved = target_gemini.resolve()
    except OSError:
        return []

    copied_files: list[Path] = []

    def _is_component_denied(part: str) -> bool:
        low = part.lower()
        return any(fnmatch.fnmatch(low, pat) for pat in DENIED_FILE_PATTERNS)

    def _is_rel_path_denied(rel_path: Path) -> bool:
        return any(_is_component_denied(part) for part in rel_path.parts)

    def _safe_copy_file(src: Path, rel_path: Path) -> None:
        if _is_rel_path_denied(rel_path):
            return

        # Do not follow source symlinks
        if src.is_symlink() or os.path.islink(src):
            return

        dest = target_gemini / rel_path

        # Reject destination if it or any intermediate directory is a symlink
        curr = target_gemini
        for part in rel_path.parts[:-1]:
            curr = curr / part
            if curr.is_symlink() or os.path.islink(curr):
                return
            if curr.exists():
                try:
                    curr_resolved = curr.resolve()
                    if not (
                        curr_resolved == target_gemini_resolved
                        or curr_resolved.is_relative_to(target_gemini_resolved)
                    ):
                        return
                except OSError:
                    return
            else:
                try:
                    parent_res = curr.parent.resolve()
                    if not (
                        parent_res == target_gemini_resolved
                        or parent_res.is_relative_to(target_gemini_resolved)
                    ):
                        return
                except OSError:
                    return
                ensure_private_directory(curr)

        # Reject if destination file itself is a symlink
        if dest.is_symlink() or os.path.islink(dest):
            return
        if dest.exists():
            try:
                dest_res = dest.resolve()
                if not (
                    dest_res == target_gemini_resolved
                    or dest_res.is_relative_to(target_gemini_resolved)
                ):
                    return
            except OSError:
                return

        ensure_private_directory(dest.parent)

        tmp_name = f".tmp_sync_{uuid.uuid4().hex}"
        tmp_file = dest.parent / tmp_name
        try:
            content = src.read_bytes()
            tmp_file.write_bytes(content)
            ensure_private_file(tmp_file)
            os.replace(tmp_file, dest)
            copied_files.append(dest)
        except OSError:
            if tmp_file.exists():
                try:
                    tmp_file.unlink()
                except OSError:
                    pass

    sync_lock = get_lock(p_dir / ".sync")
    with sync_lock:
        # 1. Sync allowlisted files
        for rel_str in ALLOWLISTED_SETTINGS_FILES:
            rel_path = Path(rel_str)
            src_path = real_gemini_dir / rel_path
            if src_path.exists() and src_path.is_file():
                _safe_copy_file(src_path, rel_path)

        # 2. Sync allowlisted directories
        for rel_dir_str in ALLOWLISTED_SETTINGS_DIRS:
            rel_dir = Path(rel_dir_str)
            src_dir = real_gemini_dir / rel_dir
            if (
                src_dir.exists()
                and src_dir.is_dir()
                and not (src_dir.is_symlink() or os.path.islink(src_dir))
            ):
                for root, dirs, files in os.walk(src_dir):
                    valid_dirs = []
                    for d in dirs:
                        d_path = Path(root) / d
                        if d_path.is_symlink() or os.path.islink(d_path):
                            continue
                        rel_d = d_path.relative_to(real_gemini_dir)
                        if _is_rel_path_denied(rel_d):
                            continue
                        valid_dirs.append(d)
                    dirs[:] = valid_dirs

                    for f in files:
                        f_path = Path(root) / f
                        if f_path.is_symlink() or os.path.islink(f_path):
                            continue
                        rel_f = f_path.relative_to(real_gemini_dir)
                        _safe_copy_file(f_path, rel_f)

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
    sync_settings: bool = True,
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

    with track_active_operation(name) as run_id:
        caller_log_file: Path | None = None
        for i, arg in enumerate(args):
            if arg in ("--log-file", "-l", "--log"):
                if i + 1 < len(args):
                    caller_log_file = Path(args[i + 1])
            elif arg.startswith(("--log-file=", "--log=")):
                caller_log_file = Path(arg.split("=", 1)[1])

        injected_log_file: Path | None = None
        cmd_args = list(args)
        if inject_log_file and caller_log_file is None:
            logs_dir = get_state_dir() / "logs" / name
            logs_dir.mkdir(parents=True, exist_ok=True)
            ensure_private_directory(logs_dir)
            injected_log_file = logs_dir / f"{run_id}.log"
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
            # Stream stdout and stderr with bounded rolling tails for health
            captured_stdout = bytearray()
            captured_stderr = bytearray()
            stdin_target = None
            try:
                if sys.stdin and hasattr(sys.stdin, "fileno"):
                    sys.stdin.fileno()
                    stdin_target = sys.stdin
            except (io.UnsupportedOperation, AttributeError, OSError):
                stdin_target = None

            proc = subprocess.Popen(
                cmd,
                env=env,
                cwd=cwd,
                stdin=stdin_target,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )

            def _forward_pipe(
                pipe: Any, out_stream: Any, captured_buf: bytearray
            ) -> None:
                try:
                    while True:
                        chunk = pipe.read(4096)
                        if not chunk:
                            break
                        try:
                            if hasattr(out_stream, "buffer"):
                                out_stream.buffer.write(chunk)
                                out_stream.buffer.flush()
                            else:
                                text_c = chunk.decode("utf-8", errors="replace")
                                out_stream.write(text_c)
                                out_stream.flush()
                        except Exception:
                            pass
                        captured_buf.extend(chunk)
                        if len(captured_buf) > 65536:
                            del captured_buf[:-65536]
                finally:
                    pipe.close()

            t_out = threading.Thread(
                target=_forward_pipe,
                args=(proc.stdout, sys.stdout, captured_stdout),
                daemon=True,
            )
            t_err = threading.Thread(
                target=_forward_pipe,
                args=(proc.stderr, sys.stderr, captured_stderr),
                daemon=True,
            )
            t_out.start()
            t_err.start()

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
                t_out.join(timeout=1.0)
                t_err.join(timeout=1.0)

            stdout_text = captured_stdout.decode("utf-8", errors="replace")
            stderr_text = captured_stderr.decode("utf-8", errors="replace")

        if update_health:
            from magy.agy import classify_run_health, read_bounded_log_tail

            effective_log = caller_log_file or injected_log_file
            log_content = ""
            if effective_log and effective_log.is_file():
                log_content = read_bounded_log_tail(effective_log, max_bytes=32768)

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
