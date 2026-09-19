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


def classify_candidate(path: Path) -> tuple[bool, str | None]:
    """Classify whether a candidate executable is direct or indirect.

    Returns:
        (is_direct, reason_if_indirect_or_invalid)
    """
    try:
        if path.is_symlink() and not path.exists():
            return False, "broken symlink"
        if not path.exists():
            return False, "file not found"
        if not path.is_file():
            return False, "not a regular file"
        if not os.access(path, os.X_OK):
            return False, "not executable"
        if path.is_symlink():
            target = path.resolve()
            if target.name.lower() != path.name.lower():
                return False, f"multicall shim pointing to '{target.name}'"
            return False, "symlink indirection"
        return True, None
    except OSError as e:
        return False, str(e)


def execute_resolver(
    resolver_argv: list[str],
    cwd: Path | None = None,
    timeout: float = 5.0,
) -> tuple[Path | None, str | None]:
    """Execute configured lookup resolver and validate direct executable output."""
    if not resolver_argv:
        return None, "empty resolver command"
    try:
        res = subprocess.run(
            resolver_argv,
            cwd=cwd,
            env=os.environ,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        if res.returncode != 0:
            err_detail = res.stderr.strip() or f"exit code {res.returncode}"
            return None, f"resolver ({' '.join(resolver_argv)} failed: {err_detail})"

        lines = [line.strip() for line in res.stdout.splitlines() if line.strip()]
        if not lines:
            return None, f"resolver ({' '.join(resolver_argv)} returned empty output)"
        if len(lines) > 1:
            return None, f"resolver ({' '.join(resolver_argv)} returned multiple lines)"

        out_str = lines[0]
        out_path = Path(out_str)
        if not out_path.is_absolute():
            cmd_str = " ".join(resolver_argv)
            return (
                None,
                f"resolver ({cmd_str} returned relative path '{out_str}')",
            )

        is_direct, reason = classify_candidate(out_path)
        if not is_direct:
            return (
                None,
                f"resolver ({' '.join(resolver_argv)} returned {reason}: '{out_str}')",
            )

        return out_path, None
    except subprocess.TimeoutExpired:
        return None, f"resolver ({' '.join(resolver_argv)} timed out after {timeout}s)"
    except OSError as e:
        return None, f"resolver ({' '.join(resolver_argv)} failed to execute: {e})"


def resolve_agy_executable(
    configured_cmd: str | None = None,
    configured_resolver: list[str] | None = None,
    path_env: str | None = None,
    cwd: Path | None = None,
) -> tuple[Path | None, str | None]:
    """Resolve direct agy executable candidate.

    Preserves invocation identity without replacing it with canonical symlink target.

    Precedence:
      1. MAGY_AGY_CMD environment variable (direct executable only)
      2. Configured resolver (agy_resolver) or configured direct path (agy_cmd)
      3. First direct agy executable found on PATH (skipping indirect/multicall shims)

    Returns (lexical_path, source_description_or_error).
    """
    env_cmd = os.environ.get("MAGY_AGY_CMD")
    if env_cmd is not None:
        if not env_cmd.strip():
            return None, "MAGY_AGY_CMD (empty path)"
        p = Path(os.path.expanduser(env_cmd))
        if not p.is_absolute():
            if "/" in env_cmd or "\\" in env_cmd:
                base_cwd = cwd if cwd is not None else Path.cwd()
                p = base_cwd / p
            else:
                which_p = shutil.which(env_cmd, path=path_env)
                if which_p:
                    p = Path(which_p)
                else:
                    return (
                        None,
                        f"MAGY_AGY_CMD ('{env_cmd}' not found or not executable)",
                    )

        is_direct, reason = classify_candidate(p)
        if not is_direct:
            return None, f"MAGY_AGY_CMD ('{env_cmd}' {reason})"
        return p, "MAGY_AGY_CMD"

    if configured_resolver is not None:
        exe, err = execute_resolver(configured_resolver, cwd=cwd)
        if exe is not None:
            return exe, "resolver"
        return None, f"config ({err})"

    if configured_cmd is not None:
        if not isinstance(configured_cmd, str):
            return None, f"config (invalid type {type(configured_cmd).__name__})"
        if not configured_cmd.strip():
            return None, "config (empty path)"

        p = Path(os.path.expanduser(configured_cmd))
        if not p.is_absolute():
            if "/" in configured_cmd or "\\" in configured_cmd:
                base_cwd = cwd if cwd is not None else Path.cwd()
                p = base_cwd / p
            else:
                which_p = shutil.which(configured_cmd, path=path_env)
                if which_p:
                    p = Path(which_p)
                else:
                    return (
                        None,
                        f"config ('{configured_cmd}' not found or not executable)",
                    )

        is_direct, reason = classify_candidate(p)
        if not is_direct:
            return None, f"config ('{configured_cmd}' {reason})"
        return p, "config"

    if path_env is None:
        path_env = os.environ.get("PATH", "")

    path_dirs = [d for d in path_env.split(os.pathsep) if d.strip()]
    rejected_candidates: list[tuple[Path, str]] = []

    exe_names = ["agy"]
    if os.name == "nt":
        pathext = [
            x.lower()
            for x in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";")
            if x.strip()
        ]
        exe_names = ["agy" + ext for ext in pathext] + ["agy"]

    for d in path_dirs:
        dir_path = Path(d)
        for name in exe_names:
            candidate = dir_path / name
            try:
                if not candidate.exists() and not candidate.is_symlink():
                    continue
            except OSError:
                continue

            is_direct, reason = classify_candidate(candidate)
            if is_direct:
                return candidate, "PATH"
            if reason != "broken symlink":
                rejected_candidates.append((candidate, reason or "indirect"))

    if rejected_candidates:
        first_cand, reason = rejected_candidates[0]
        return (
            None,
            f"PATH ('{first_cand}' is an indirect launcher ({reason}); "
            "configure a direct path or agy_resolver)",
        )

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


def get_agy_capabilities(
    executable: Path | None, timeout: float = 5.0
) -> dict[str, bool]:
    """Inspect agy executable help output to detect supported capabilities.

    Returns dict with keys:
      - 'supports_json_output': True if --output-format is supported
      - 'supports_auto_approval': True if --dangerously-skip-permissions is supported
      - 'supports_add_dir': True if --add-dir is supported
    """
    default_caps = {
        "supports_json_output": False,
        "supports_auto_approval": False,
        "supports_add_dir": False,
    }
    if executable is None:
        return default_caps

    try:
        res = subprocess.run(
            [str(executable), "--help"],
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        help_text = (res.stdout or "") + " " + (res.stderr or "")
        return {
            "supports_json_output": "--output-format" in help_text,
            "supports_auto_approval": "--dangerously-skip-permissions" in help_text,
            "supports_add_dir": "--add-dir" in help_text,
        }
    except (OSError, subprocess.TimeoutExpired):
        return default_caps


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
    rejected_candidate: Path | None = None
    resolver_cmd: list[str] | None = None

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
    exe, source = resolve_agy_executable(
        configured_cmd=cfg.agy_cmd,
        configured_resolver=cfg.agy_resolver,
    )

    missing: list[str] = []
    rejected_candidate: Path | None = None

    if cfg_result.error:
        missing.append(f"Config error: {cfg_result.error}")

    if exe is None:
        if source:
            missing.append(f"Agy executable invalid: {source}")
            if "indirect launcher" in source and "('" in source:
                try:
                    cand_str = source.split("('", 1)[1].split("'", 1)[0]
                    rejected_candidate = Path(cand_str)
                except Exception:
                    pass
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
        rejected_candidate=rejected_candidate,
        resolver_cmd=cfg.agy_resolver,
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
    "quota exhausted",
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
    """Parse provider retry timing in seconds, minutes, or hours (latest match)."""
    matches = list(RETRY_PATTERN.finditer(text))
    if not matches:
        return None
    match = matches[-1]
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
    return val * multiplier


def sanitize_reason(raw: str) -> str:
    """Sanitize and redact sensitive tokens, headers, emails, credentials, and paths."""
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

    s = first_line

    # 1. Redact the complete Authorization header, including compound schemes.
    def _redact_auth_header(m: re.Match) -> str:
        scheme = m.group(1)
        if scheme:
            return f"Authorization: {scheme.strip()} [REDACTED]"
        return "Authorization: [REDACTED]"

    s = re.sub(
        r"(?i)\bauthorization\s*:\s*"
        r"(?:(bearer|basic|digest|aws4-hmac-sha256)\s+)?[^\r\n]*$",
        _redact_auth_header,
        s,
    )
    # Standalone Bearer or Basic token
    s = re.sub(
        r"(?i)\b(bearer|basic)\s+[A-Za-z0-9._~+/=-]+",
        lambda m: f"{m.group(1)} [REDACTED]",
        s,
    )

    # 2. Redact URL with embedded credentials: http(s)://user:pass@host
    s = re.sub(
        r"(https?://)([^:\s/@]+):([^@\s/]+)@",
        r"\1[REDACTED_USER]:[REDACTED_PASS]@",
        s,
    )
    # Redact URL query parameters
    s = re.sub(r"(\?[^\s#]*)", r"?[REDACTED_QUERY]", s)

    # 3. Redact common credential keys including provider prefixes and compound names
    cred_names = (
        r"(?:[A-Za-z0-9_-]+[_-])?"
        r"(?:api[_-]?key|access[_-]?token|refresh[_-]?token|secret[_-]?access[_-]?key|"
        r"secret[_-]?key|client[_-]?secret|client[_-]?id|auth(?:[_-]?token)?|"
        r"authorization|credential|signature|password|passwd|apiKey|token)"
    )
    # JSON quoted key/value
    s = re.sub(
        rf'(?i)"({cred_names})"\s*:\s*"[^"]*"',
        r'"\1": "[REDACTED]"',
        s,
    )
    s = re.sub(
        rf'(?i)"({cred_names})"\s*:\s*([^",\s}}]+)',
        r'"\1": [REDACTED]',
        s,
    )
    # Python-style quoted key/value
    s = re.sub(
        rf"""(?i)'({cred_names})'\s*:\s*(?:'[^']*'|"[^"]*")""",
        r"'\1': '[REDACTED]'",
        s,
    )
    # Key-value pairs
    s = re.sub(
        rf"""(?i)\b(?!authorization\s*:)({cred_names})\s*[=:]\s*(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
        r"\1=[REDACTED]",
        s,
    )
    # CLI options
    s = re.sub(
        r"""(?i)(--?[A-Za-z0-9_-]*(?:api[_-]?key|token|secret|password|auth)[A-Za-z0-9_-]*)(?:\s*=\s*|\s+)(?:"[^"]*"|'[^']*'|[^\s,;]+)""",
        r"\1=[REDACTED]",
        s,
    )

    # 4. Redact email addresses
    s = re.sub(
        r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "[REDACTED_EMAIL]",
        s,
    )

    # 5. Redact user home paths
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
            f.seek(seek_pos, io.SEEK_SET)
            data = f.read()
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
    """Classify the health outcome of an agy invocation."""
    if exit_code == 0:
        return HealthClassification(health="healthy", is_success=True)

    if config is None:
        try:
            cfg_res = load_config_result()
            config = cfg_res.config if not cfg_res.error else MagyConfig()
        except Exception:
            config = MagyConfig()

    def _classify_stream(text: str) -> HealthClassification | None:
        if not text:
            return None
        text_lower = text.lower()

        def _latest_pos(patterns: tuple[str, ...]) -> int:
            latest = -1
            for p in patterns:
                if p == "429":
                    for m in re.finditer(r"\b429\b", text_lower):
                        if m.start() > latest:
                            latest = m.start()
                else:
                    idx = text_lower.rfind(p.lower())
                    if idx > latest:
                        latest = idx
            return latest

        pos_auth = _latest_pos(AUTH_PATTERNS)
        pos_quota = _latest_pos(QUOTA_PATTERNS)
        pos_rate = _latest_pos(RATE_PATTERNS)
        pos_timeout = _latest_pos(TIMEOUT_PATTERNS)

        best_cat = None
        max_pos = -1
        for cat, pos in (
            ("auth", pos_auth),
            ("quota", pos_quota),
            ("rate", pos_rate),
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
        elif best_cat == "quota":
            return HealthClassification(
                health="quota-exhausted",
                cooldown_seconds=config.cooldown_quota,
                reason="Quota exhausted",
            )
        elif best_cat == "rate":
            parsed_cooldown = parse_retry_seconds(text)
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
        elif best_cat == "timeout":
            return HealthClassification(
                health="timeout",
                cooldown_seconds=config.cooldown_timeout,
                reason="Request timed out",
            )
        return None

    bounded_stdout = stdout[-32768:] if len(stdout) > 32768 else stdout
    bounded_stderr = stderr[-32768:] if len(stderr) > 32768 else stderr
    bounded_log = log_content[-32768:] if len(log_content) > 32768 else log_content

    cls_stdout = _classify_stream(bounded_stdout)
    cls_stderr = _classify_stream(bounded_stderr)
    cls_log = _classify_stream(bounded_log)

    detected = [c for c in (cls_stdout, cls_stderr, cls_log) if c is not None]

    if detected:
        # Conservative precedence across sources: auth > quota > rate > timeout
        categories = ("auth-required", "quota-exhausted", "rate-limited", "timeout")
        for cat_name in categories:
            for c in detected:
                if c.health == cat_name:
                    return c

    # Unknown provider text is not persisted because it can contain undocumented
    # credential formats. Detailed diagnostics remain in the private run log.
    sanitized = f"Command failed with exit code {exit_code}"
    return HealthClassification(
        health="unknown-failure",
        cooldown_seconds=config.cooldown_unknown,
        reason=sanitized,
    )
