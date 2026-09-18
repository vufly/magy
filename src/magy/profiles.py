import ntpath
import os
import subprocess
from pathlib import Path
from typing import Any

from magy.agy import resolve_agy_executable
from magy.config import get_data_dir, load_config_result
from magy.storage import (
    ensure_private_directory,
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


def get_profiles_dir() -> Path:
    """Return the managed profiles root directory with owner-only permissions."""
    path = get_data_dir() / "profiles"
    return ensure_private_directory(path)


def get_profile_dir(name: str) -> Path:
    """Return the directory for a specific profile."""
    validated = validate_profile_name(name)
    return get_profiles_dir() / validated


def get_profile_home_dir(name: str) -> Path:
    """Return the synthetic home directory for a profile."""
    return get_profile_dir(name) / "home"


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
) -> Any:
    """Run an Agy command within the profile's isolated environment."""
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

    cmd = [str(executable), *args]

    if capture_output:
        return subprocess.run(
            cmd,
            env=env,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )

    res = subprocess.run(
        cmd,
        env=env,
        cwd=cwd,
        timeout=timeout,
        check=False,
    )
    return res.returncode
