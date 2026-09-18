import fnmatch
import ntpath
import os
import shutil
import signal
import subprocess
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
from magy.config import get_data_dir, get_state_dir, load_config, load_config_result
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
    "GEMINI.md",
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
    incarnation_id: str = field(default_factory=lambda: uuid.uuid4().hex)
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
            incarnation_id=data.get("incarnation_id") or uuid.uuid4().hex,
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


def get_profile_lease_path(name: str) -> Path:
    """Return path to the lifecycle lease file for a profile."""
    validated = validate_profile_name(name)
    leases_dir = get_state_dir() / "leases"
    leases_dir.mkdir(parents=True, exist_ok=True)
    ensure_private_directory(leases_dir)
    return leases_dir / f"{validated}.lease"


@contextmanager
def acquire_profile_lease(name: str, exclusive: bool = False):
    """Acquire a lifecycle lease on a profile.

    Shared (exclusive=False) lease is held during active profile operations.
    Exclusive (exclusive=True) lease is held during profile removal or mutation.
    """
    validated = validate_profile_name(name)
    lease_file = get_profile_lease_path(validated)

    fd = os.open(str(lease_file), os.O_RDWR | os.O_CREAT, 0o600)
    ensure_private_file(lease_file)

    if fcntl is not None:
        lock_flags = (
            (fcntl.LOCK_EX | fcntl.LOCK_NB)
            if exclusive
            else (fcntl.LOCK_SH | fcntl.LOCK_NB)
        )
        try:
            fcntl.flock(fd, lock_flags)
        except (BlockingIOError, OSError) as e:
            os.close(fd)
            if exclusive:
                raise RuntimeError(
                    f"Cannot remove profile '{validated}': "
                    "active operations are running"
                ) from e
            else:
                raise RuntimeError(
                    f"Cannot operate on profile '{validated}': "
                    "profile is locked by another operation"
                ) from e

    try:
        yield
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


@contextmanager
def track_active_operation(name: str):
    """Track an active operation on a profile using the shared lifecycle lease."""
    validated = validate_profile_name(name)
    run_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
    with acquire_profile_lease(validated, exclusive=False):
        yield run_id


def get_active_operation_count(name: str) -> int:
    """Return count of currently active operations for a profile (0 or >=1)."""
    validated = validate_profile_name(name)
    lease_file = get_profile_lease_path(validated)
    if not lease_file.exists():
        return 0

    try:
        fd = os.open(str(lease_file), os.O_RDWR)
    except OSError:
        return 0

    if fcntl is None:
        os.close(fd)
        return 0

    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)
        return 0
    except (BlockingIOError, OSError):
        os.close(fd)
        return 1


def add_profile(
    name: str,
    kind: str = "managed",
    home_dir: Path | str | None = None,
) -> ProfileMetadata:
    """Register a new profile (managed or external)."""
    validated = validate_profile_name(name)
    path = get_registry_file_path()

    with acquire_profile_lease(validated, exclusive=True):
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
                incarnation_id=uuid.uuid4().hex,
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

    with acquire_profile_lease(validated, exclusive=True):
        existing = get_profile(validated)
        if existing is None:
            raise KeyError(f"Profile '{validated}' does not exist")

        target_incarnation = existing.incarnation_id

        # Stage directory deletion for managed profiles
        deleting_dir: Path | None = None
        p_dir = get_profile_dir(validated)
        if existing.kind == "managed" and p_dir.exists():
            stage_name = f".deleting_{validated}_{uuid.uuid4().hex[:8]}"
            deleting_dir = p_dir.parent / stage_name
            try:
                p_dir.rename(deleting_dir)
            except OSError as e:
                raise OSError(
                    f"Failed to stage profile directory for removal: {e}"
                ) from e

        def _update(data: Any) -> Any:
            if not isinstance(data, dict):
                return data
            profiles = data.get("profiles", {})
            curr = profiles.get(validated)
            if curr is None:
                raise KeyError(f"Profile '{validated}' does not exist")
            if curr.get("incarnation_id") != target_incarnation:
                raise ValueError(
                    f"Profile '{validated}' was modified or recreated concurrently "
                    "(incarnation mismatch)"
                )
            profiles.pop(validated, None)
            return data

        try:
            update_json(path, _update, default={"version": 1, "profiles": {}})
        except Exception:
            if deleting_dir and deleting_dir.exists():
                try:
                    deleting_dir.rename(p_dir)
                except Exception:
                    pass
            raise

        # Clean up staged managed directory
        if deleting_dir and deleting_dir.exists():
            try:
                shutil.rmtree(deleting_dir)
            except OSError as e:
                raise OSError(
                    f"Failed to remove profile directory {deleting_dir}: {e}"
                ) from e

        # Clean up runs directory
        run_dir = get_state_dir() / "runs" / validated
        if run_dir.exists():
            try:
                shutil.rmtree(run_dir)
            except OSError:
                pass


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
        meta.last_selected_at = max(now, meta.last_selected_at or 0.0)
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

    # Reject if destination root or parents are symlinks
    if p_dir.is_symlink() or os.path.islink(p_dir):
        raise ValueError(f"Profile directory cannot be a symlink: {p_dir}")
    if p_home.is_symlink() or os.path.islink(p_home):
        raise ValueError(f"Profile home directory cannot be a symlink: {p_home}")
    if target_gemini.is_symlink() or os.path.islink(target_gemini):
        raise ValueError(
            f"Target profile .gemini directory cannot be a symlink: {target_gemini}"
        )

    if real_gemini_dir is None:
        env_gemini = os.environ.get("MAGY_REAL_GEMINI")
        if env_gemini:
            real_gemini_dir = Path(env_gemini)
        elif os.environ.get("MAGY_REAL_HOME"):
            real_gemini_dir = Path(os.environ["MAGY_REAL_HOME"]) / ".gemini"
        else:
            real_gemini_dir = Path.home() / ".gemini"

    if real_gemini_dir.is_symlink() or os.path.islink(real_gemini_dir):
        raise ValueError(
            f"Source .gemini directory cannot be a symlink: {real_gemini_dir}"
        )

    if not real_gemini_dir.exists():
        return []

    ensure_private_directory(target_gemini)

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
            fd = os.open(str(tmp_file), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "wb") as f:
                f.write(content)
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
        validate_profile_layout(name)

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
                        if d_path == real_gemini_dir / "config" / "skills":
                            continue
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

        # 3. Symlink skills directory if available in real .gemini
        skills_src: Path | None = None
        if (real_gemini_dir / "config" / "skills").exists():
            skills_src = real_gemini_dir / "config" / "skills"
        elif (real_gemini_dir / "skills").exists():
            skills_src = real_gemini_dir / "skills"

        if skills_src is not None:
            skills_dest = target_gemini / "config" / "skills"
            ensure_private_directory(skills_dest.parent)
            if skills_dest.is_symlink() or os.path.islink(skills_dest):
                try:
                    current_target = os.readlink(str(skills_dest))
                    if current_target != str(skills_src):
                        skills_dest.unlink()
                        skills_dest.symlink_to(skills_src)
                except OSError:
                    pass
            elif not skills_dest.exists():
                try:
                    skills_dest.symlink_to(skills_src)
                except OSError:
                    pass

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
            p_home = get_profile_home_dir(name)
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
    if os_type != "nt":
        p_home_path = Path(p_home_str)
        if "XDG_CONFIG_HOME" not in env:
            env["XDG_CONFIG_HOME"] = str(p_home_path / ".config")
        if "XDG_DATA_HOME" not in env:
            env["XDG_DATA_HOME"] = str(p_home_path / ".local" / "share")
        if "XDG_CACHE_HOME" not in env:
            env["XDG_CACHE_HOME"] = str(p_home_path / ".cache")
        if "XDG_STATE_HOME" not in env:
            env["XDG_STATE_HOME"] = str(p_home_path / ".local" / "state")
    return env


def _kill_process_tree(proc: subprocess.Popen, grace: float = 1.0) -> None:
    """Terminate child process and all descendants, escalating to SIGKILL."""
    if proc.poll() is not None:
        return

    pid = proc.pid
    if os.name != "nt":
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.terminate()
            except OSError:
                pass

        deadline = time.time() + grace
        while time.time() < deadline:
            if proc.poll() is not None:
                return
            time.sleep(0.05)

        try:
            os.killpg(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError, OSError):
            try:
                proc.kill()
            except OSError:
                pass

        try:
            proc.wait(timeout=1.0)
        except (subprocess.TimeoutExpired, OSError):
            pass
    else:
        try:
            proc.terminate()
        except OSError:
            pass
        try:
            proc.wait(timeout=grace)
        except (subprocess.TimeoutExpired, OSError):
            try:
                proc.kill()
                proc.wait(timeout=1.0)
            except OSError:
                pass


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
    validated = validate_profile_name(name)

    with acquire_profile_lease(validated, exclusive=False):
        profile = get_profile(validated)
        if profile is not None and not profile.enabled:
            raise ValueError(f"Profile '{validated}' is disabled")

        if profile is None or profile.kind == "managed":
            validate_profile_layout(validated)
            if sync_settings:
                sync_profile_settings(validated)

        if executable is None:
            cfg_res = load_config_result()
            if cfg_res.error:
                raise ValueError(f"Invalid configuration: {cfg_res.error}")
            exe, source = resolve_agy_executable(
                configured_cmd=cfg_res.config.agy_cmd,
                configured_resolver=cfg_res.config.agy_resolver,
                cwd=cwd,
            )
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

        env = build_profile_env(validated)
        if env_overrides:
            env.update(env_overrides)

        caller_log_file: Path | None = None
        for i, arg in enumerate(args):
            if arg in ("--log-file", "-l", "--log"):
                if i + 1 < len(args):
                    caller_log_file = Path(args[i + 1])
            elif arg.startswith(("--log-file=", "--log=")):
                caller_log_file = Path(arg.split("=", 1)[1])

        effective_log_file: Path | None = None
        if caller_log_file is not None:
            base_cwd = cwd if cwd is not None else Path.cwd()
            effective_log_file = (
                (base_cwd / caller_log_file)
                if not caller_log_file.is_absolute()
                else caller_log_file
            )

        run_id = f"{int(time.time() * 1000)}_{uuid.uuid4().hex[:8]}"
        injected_log_file: Path | None = None
        cmd_args = list(args)
        if (inject_log_file or update_health) and caller_log_file is None:
            logs_dir = get_state_dir() / "logs" / validated
            logs_dir.mkdir(parents=True, exist_ok=True)
            ensure_private_directory(logs_dir)
            injected_log_file = logs_dir / f"{run_id}.log"
            if "--" in args:
                dash_idx = args.index("--")
                cmd_args = [
                    *args[:dash_idx],
                    "--log-file",
                    str(injected_log_file),
                    *args[dash_idx:],
                ]
            else:
                cmd_args = [*args, "--log-file", str(injected_log_file)]
            effective_log_file = injected_log_file

        cmd = [str(executable), *cmd_args]

        popen_kwargs: dict[str, Any] = {
            "env": env,
            "cwd": cwd,
        }
        if os.name != "nt":
            popen_kwargs["process_group"] = 0

        if capture_output:
            popen_kwargs["stdout"] = subprocess.PIPE
            popen_kwargs["stderr"] = subprocess.PIPE
            popen_kwargs["stdin"] = subprocess.PIPE
        else:
            popen_kwargs["stdin"] = None
            popen_kwargs["stdout"] = None
            popen_kwargs["stderr"] = None

        proc = subprocess.Popen(cmd, **popen_kwargs)

        def _forward_signal(signum: int, frame: Any) -> None:
            if os.name != "nt":
                try:
                    os.killpg(proc.pid, signum)
                except OSError:
                    pass
            else:
                try:
                    proc.send_signal(signum)
                except OSError:
                    pass

        old_sigint = None
        old_sigterm = None
        try:
            old_sigint = signal.signal(signal.SIGINT, _forward_signal)
        except (ValueError, AttributeError):
            pass
        try:
            old_sigterm = signal.signal(signal.SIGTERM, _forward_signal)
        except (ValueError, AttributeError):
            pass

        timed_out = False
        try:
            if capture_output:
                stdout_bytes, stderr_bytes = proc.communicate(timeout=timeout)
                stdout_text = (
                    stdout_bytes.decode("utf-8", errors="replace")
                    if stdout_bytes
                    else ""
                )
                stderr_text = (
                    stderr_bytes.decode("utf-8", errors="replace")
                    if stderr_bytes
                    else ""
                )
                retcode = proc.returncode
            else:
                retcode = proc.wait(timeout=timeout)
                stdout_text = ""
                stderr_text = ""
        except subprocess.TimeoutExpired as exc:
            timed_out = True
            _kill_process_tree(proc, grace=1.0)
            retcode = 124
            if update_health:
                cfg = load_config()
                update_profile_health(
                    validated,
                    health="timeout",
                    cooldown_seconds=cfg.cooldown_timeout,
                    reason="Command timed out",
                    is_success=False,
                )
            raise exc
        finally:
            if old_sigint is not None:
                signal.signal(signal.SIGINT, old_sigint)
            if old_sigterm is not None:
                signal.signal(signal.SIGTERM, old_sigterm)
            if proc.poll() is None:
                _kill_process_tree(proc, grace=1.0)

        if retcode is not None and retcode < 0:
            retcode = 128 + abs(retcode)

        if update_health and not timed_out:
            from magy.agy import classify_run_health, read_bounded_log_tail

            log_content = ""
            if effective_log_file and effective_log_file.is_file():
                log_content = read_bounded_log_tail(effective_log_file, max_bytes=32768)

            classification = classify_run_health(
                retcode,
                stdout=stdout_text,
                stderr=stderr_text,
                log_content=log_content,
            )
            update_profile_health(
                validated,
                classification.health,
                cooldown_seconds=classification.cooldown_seconds,
                reason=classification.reason,
                is_success=classification.is_success,
            )

        if capture_output:
            return subprocess.CompletedProcess(
                args=cmd,
                returncode=retcode,
                stdout=stdout_text,
                stderr=stderr_text,
            )
        return retcode
