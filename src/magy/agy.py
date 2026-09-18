import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from magy.config import (
    get_config_dir,
    get_data_dir,
    get_state_dir,
    load_config_result,
)


def _is_executable_file(path: Path) -> bool:
    return path.is_file() and os.access(path, os.X_OK)


def resolve_agy_executable(
    configured_cmd: str | None = None,
    path_env: str | None = None,
) -> tuple[Path | None, str | None]:
    """Resolve agy executable following precedence:

    1. MAGY_AGY_CMD environment variable
    2. Configured executable path
    3. agy on PATH

    Returns (resolved_path, source_description).
    """
    env_cmd = os.environ.get("MAGY_AGY_CMD")
    if env_cmd:
        p = Path(env_cmd).expanduser()
        if _is_executable_file(p):
            return p.resolve(), "MAGY_AGY_CMD"
        which_p = shutil.which(env_cmd, path=path_env)
        if which_p:
            return Path(which_p).resolve(), "MAGY_AGY_CMD"
        return None, f"MAGY_AGY_CMD ('{env_cmd}' not found or not executable)"

    if configured_cmd is not None:
        if not isinstance(configured_cmd, str):
            return None, f"config (invalid type {type(configured_cmd).__name__})"
        if not configured_cmd.strip():
            return None, "config (empty path)"
        p = Path(configured_cmd).expanduser()
        if _is_executable_file(p):
            return p.resolve(), "config"
        which_p = shutil.which(configured_cmd, path=path_env)
        if which_p:
            return Path(which_p).resolve(), "config"
        return None, f"config ('{configured_cmd}' not found or not executable)"

    which_p = shutil.which("agy", path=path_env)
    if which_p:
        return Path(which_p).resolve(), "PATH"

    return None, None


def get_agy_version(executable: Path, timeout: float = 5.0) -> str | None:
    """Query agy executable version with --version."""
    try:
        # Run without modified profile home variables
        res = subprocess.run(
            [str(executable), "--version"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode == 0:
            out = res.stdout.strip()
            if not out and res.stderr:
                out = res.stderr.strip()
            return out or "unknown"
    except (OSError, subprocess.TimeoutExpired):
        pass
    return None


@dataclass
class AgyDiagnostics:
    magy_version: str
    python_version: str
    platform_info: str
    config_dir: Path | None
    data_dir: Path | None
    state_dir: Path | None
    config_error: str | None
    executable: Path | None
    discovery_source: str | None
    agy_version: str | None
    missing_prerequisites: list[str]

    @property
    def is_healthy(self) -> bool:
        return len(self.missing_prerequisites) == 0


def collect_diagnostics() -> AgyDiagnostics:
    """Collect system diagnostics without reading or printing credential contents."""
    try:
        m_ver = pkg_version("magy")
    except Exception:
        m_ver = "0.1.0"

    cfg_result = load_config_result()
    cfg = cfg_result.config
    exe, source = resolve_agy_executable(configured_cmd=cfg.agy_cmd)

    missing: list[str] = []

    if cfg_result.error:
        missing.append(f"Config error: {cfg_result.error}")

    if exe is None:
        if source:
            missing.append(f"Agy executable invalid: {source}")
        else:
            missing.append(
                "Agy executable not found (checked MAGY_AGY_CMD, config, and PATH)"
            )
        agy_ver = None
    else:
        agy_ver = get_agy_version(exe)
        if agy_ver is None:
            missing.append(f"Unable to execute '{exe} --version'")

    def _safe_get_root(
        name: str, getter_fn: Any
    ) -> tuple[Path | None, str | None]:
        target_path: Path | None = None
        try:
            target_path = getter_fn(create=False)
        except Exception:
            pass

        try:
            p = getter_fn(create=True)
            if not os.access(p, os.W_OK):
                return p, f"Directory not writable: {name} ({p})"
            return p, None
        except PermissionError as e:
            p_str = f" ({target_path})" if target_path else ""
            return (
                target_path,
                f"Permission denied accessing {name} directory{p_str}: {e}",
            )
        except OSError as e:
            p_str = f" ({target_path})" if target_path else ""
            return target_path, f"Error accessing {name} directory{p_str}: {e}"

    c_dir, c_err = _safe_get_root("config", get_config_dir)
    d_dir, d_err = _safe_get_root("data", get_data_dir)
    s_dir, s_err = _safe_get_root("state", get_state_dir)

    for err in (c_err, d_err, s_err):
        if err:
            missing.append(err)

    return AgyDiagnostics(
        magy_version=m_ver,
        python_version=sys.version.split()[0],
        platform_info=(
            f"{platform.system()} {platform.release()} ({platform.machine()})"
        ),
        config_dir=c_dir,
        data_dir=d_dir,
        state_dir=s_dir,
        config_error=cfg_result.error,
        executable=exe,
        discovery_source=source,
        agy_version=agy_ver,
        missing_prerequisites=missing,
    )
