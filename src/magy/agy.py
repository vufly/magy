import io
import os
import platform
import re
import shutil
import subprocess
import sys
from dataclasses import dataclass
from importlib.metadata import version as pkg_version
from pathlib import Path
from typing import Any

from magy.config import (
    MagyConfig,
    get_config_dir,
    get_data_dir,
    get_state_dir,
    load_config_result,
)
from magy.storage import safe_expand_path


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
        try:
            p = safe_expand_path(env_cmd)
            if _is_executable_file(p):
                return p.resolve(), "MAGY_AGY_CMD"
        except (RuntimeError, OSError):
            pass
        which_p = shutil.which(env_cmd, path=path_env)
        if which_p:
            return Path(which_p).resolve(), "MAGY_AGY_CMD"
        return None, f"MAGY_AGY_CMD ('{env_cmd}' not found or not executable)"

    if configured_cmd is not None:
        if not isinstance(configured_cmd, str):
            return None, f"config (invalid type {type(configured_cmd).__name__})"
        if not configured_cmd.strip():
            return None, "config (empty path)"
        try:
            p = safe_expand_path(configured_cmd)
            if _is_executable_file(p):
                return p.resolve(), "config"
        except (RuntimeError, OSError):
            pass
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

    def _safe_get_root(name: str, getter_fn: Any) -> tuple[Path | None, str | None]:
        target_path: Path | None = None
        try:
            target_path = getter_fn(create=False)
        except (RuntimeError, OSError):
            target_path = None
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
        except (RuntimeError, OSError) as e:
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


@dataclass
class HealthClassification:
    health: str
    cooldown_seconds: float | None = None
    reason: str | None = None
    is_success: bool = False


AUTH_PATTERNS = (
    "please sign in",
    "authentication required",
    "auth login",
    "credentials expired",
    "unauthenticated",
    "token expired",
    "invalid credentials",
    "authentication failed",
)

RATE_PATTERNS = (
    "429",
    "rate limit reached",
    "resource exhausted: rate limit",
    "resource_exhausted",
    "too many requests",
    "rate_limit_exceeded",
    "rate limit exceeded",
    "rate_limited",
)

QUOTA_PATTERNS = (
    "exceeded your current quota",
    "quota exceeded",
    "check your plan and billing details",
    "insufficient_quota",
    "out of quota",
)

TIMEOUT_PATTERNS = (
    "request timed out",
    "deadline exceeded",
    "timeout after",
    "timed out",
)

RETRY_PATTERN = re.compile(
    r"(?:retry|try again)\s+(?:in|after)\s+([0-9]+(?:\.[0-9]+)?)\s*"
    r"(h(?:ou)?rs?|m(?:in(?:ute)?)?s?|s(?:ec(?:ond)?)?s?)?\b",
    re.IGNORECASE,
)


def parse_retry_seconds(text: str) -> float | None:
    """Parse provider retry timing in seconds, minutes, or hours."""
    match = RETRY_PATTERN.search(text)
    if not match:
        return None
    try:
        val = float(match.group(1))
    except (ValueError, TypeError):
        return None
    unit = (match.group(2) or "s").lower()
    if unit.startswith("h"):
        multiplier = 3600.0
    elif unit.startswith("m"):
        multiplier = 60.0
    else:
        multiplier = 1.0
    return max(1.0, val * multiplier)


def sanitize_reason(raw: str) -> str:
    """Sanitize and redact sensitive tokens, headers, emails, and paths."""
    if not raw:
        return "Unknown failure"

    first_line = ""
    for line in raw.splitlines():
        line_clean = line.strip()
        if line_clean and not line_clean.startswith("Traceback"):
            first_line = line_clean
            break
    if not first_line:
        first_line = raw.strip()

    # Redact authorization headers and bearer tokens
    s = re.sub(
        r"(?i)\b(bearer\s+)[A-Za-z0-9._~+/-]+",
        r"\1[REDACTED]",
        first_line,
    )
    s = re.sub(
        r"(?i)\b(authorization:\s*(?:bearer\s+)?)[^\s,;]+",
        r"\1[REDACTED]",
        s,
    )
    # Redact tokens, keys, secrets, passwords
    s = re.sub(
        r"(?i)\b(token|api_?key|key|secret|password|passwd|auth)[=:\s]+(['\"]?)[^\s'\"]+\2",
        r"\1=[REDACTED]",
        s,
    )
    # Redact email addresses
    s = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[REDACTED_EMAIL]",
        s,
    )
    # Redact URL query parameters
    s = re.sub(r"https?://[^\s?#]+(\?[^\s#]*)", r"[REDACTED_URL]", s)
    # Redact user home paths
    s = re.sub(r"/(?:home|Users)/[A-Za-z0-9._-]+", "~", s)

    # Normalize whitespace and bound length to 120 chars
    s = " ".join(s.split())
    return s[:120].strip() or "Unknown failure"


def read_bounded_log_tail(path: Path | str, max_bytes: int = 32768) -> str:
    """Read a bounded tail from a log file without loading the entire file."""
    p = Path(path)
    if not p.is_file():
        return ""
    try:
        with p.open("rb") as f:
            f.seek(0, io.SEEK_END)
            size = f.tell()
            seek_pos = max(0, size - max_bytes)
            f.seek(seek_pos)
            data = f.read(max_bytes)
            return data.decode("utf-8", errors="replace")
    except OSError:
        return ""


def classify_run_health(
    exit_code: int,
    stdout: str = "",
    stderr: str = "",
    log_content: str = "",
    config: MagyConfig | None = None,
) -> HealthClassification:
    """Classify the latest relevant health signal from execution results."""
    if exit_code == 0:
        return HealthClassification(health="healthy", is_success=True)

    if config is None:
        try:
            cfg_res = load_config_result()
            config = cfg_res.config if not cfg_res.error else MagyConfig()
        except Exception:
            config = MagyConfig()

    bounded_stdout = stdout[-32768:] if len(stdout) > 32768 else stdout
    bounded_stderr = stderr[-32768:] if len(stderr) > 32768 else stderr
    bounded_log = log_content[-32768:] if len(log_content) > 32768 else log_content
    combined = f"{bounded_stdout}\n{bounded_stderr}\n{bounded_log}".lower()

    def _latest_pos(patterns: tuple[str, ...]) -> int:
        latest = -1
        for p in patterns:
            if p == "429":
                for m in re.finditer(r"\b429\b", combined):
                    if m.start() > latest:
                        latest = m.start()
            else:
                idx = combined.rfind(p)
                if idx > latest:
                    latest = idx
        return latest

    pos_auth = _latest_pos(AUTH_PATTERNS)
    pos_rate = _latest_pos(RATE_PATTERNS)
    pos_quota = _latest_pos(QUOTA_PATTERNS)
    pos_timeout = _latest_pos(TIMEOUT_PATTERNS)

    best_cat = None
    max_pos = -1

    for cat, pos in (
        ("auth", pos_auth),
        ("rate", pos_rate),
        ("quota", pos_quota),
        ("timeout", pos_timeout),
    ):
        if pos > max_pos:
            max_pos = pos
            best_cat = cat

    if best_cat == "auth":
        return HealthClassification(
            health="auth-required",
            cooldown_seconds=config.cooldown_auth,
            reason="Authentication required",
        )
    elif best_cat == "rate":
        parsed_cooldown = parse_retry_seconds(combined)
        cooldown = (
            parsed_cooldown
            if parsed_cooldown is not None
            else config.cooldown_rate_limit
        )
        return HealthClassification(
            health="rate-limited",
            cooldown_seconds=cooldown,
            reason=f"Rate limit reached (retry in {cooldown:.0f}s)",
        )
    elif best_cat == "quota":
        return HealthClassification(
            health="quota-exhausted",
            cooldown_seconds=config.cooldown_quota,
            reason="Quota exhausted",
        )
    elif best_cat == "timeout":
        return HealthClassification(
            health="timeout",
            cooldown_seconds=config.cooldown_timeout,
            reason="Request timed out",
        )

    # Unknown failure
    raw_err = bounded_stderr or bounded_stdout or bounded_log
    sanitized = sanitize_reason(raw_err)
    if not sanitized or sanitized == "Unknown failure":
        sanitized = f"Command failed with exit code {exit_code}"
    return HealthClassification(
        health="unknown-failure",
        cooldown_seconds=config.cooldown_unknown,
        reason=sanitized,
    )
