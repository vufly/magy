import json
import os
import signal
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock

from magy.config import get_state_dir
from magy.profiles import load_profiles
from magy.storage import (
    atomic_write_json,
    ensure_private_directory,
    ensure_private_file,
    get_lock,
    read_json,
    update_json,
    validate_run_id,
)

TERMINAL_STATUSES = frozenset({"completed", "failed", "timed_out", "cancelled"})


@dataclass
class RunRequest:
    run_id: str
    prompt: str
    workspace: str | None = None
    profile: str | None = None
    model: str | None = None
    agent: str | None = None
    effort: str | None = None
    mode: str | None = None
    timeout: float | None = None
    sandbox: bool | None = None
    additional_dirs: list[str] | None = None
    auto_approval: bool = True
    idempotency_key: str | None = None
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunRequest":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class RunState:
    run_id: str
    status: str = "queued"
    profile: str | None = None
    profile_incarnation_id: str | None = None
    worker_pid: int | None = None
    child_pid: int | None = None
    child_pgid: int | None = None
    exit_code: int | None = None
    error: str | None = None
    cooldown_seconds: float | None = None
    health_classification: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    stdout_path: str = ""
    stderr_path: str = ""
    log_path: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RunState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class RunStatus:
    run_id: str
    status: str
    profile: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None
    exit_code: int | None = None
    error: str | None = None
    retryable: bool = False
    suggested_profile: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class RunResult:
    run_id: str
    status: str
    exit_code: int | None = None
    content: str = ""
    offset: int = 0
    next_offset: int = 0
    eof: bool = False
    is_json: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def get_runs_dir() -> Path:
    """Return root directory for durable runs."""
    d = get_state_dir() / "runs"
    ensure_private_directory(d)
    return d


def get_run_dir(run_id: str) -> Path:
    """Return validated directory path for a run."""
    validated = validate_run_id(run_id)
    return get_runs_dir() / validated


def get_run_lock(run_id: str) -> FileLock:
    """Return FileLock for synchronizing run state mutations."""
    run_dir = get_run_dir(run_id)
    ensure_private_directory(run_dir)
    return get_lock(run_dir / ".run.lock")


def get_idempotency_file_path() -> Path:
    """Return path to idempotency registry."""
    return get_state_dir() / "idempotency.json"


def find_run_by_idempotency_key(key: str) -> str | None:
    """Look up run ID for an idempotency key if its run state still exists."""
    if not key:
        return None
    lock = get_lock(get_state_dir() / ".idempotency.lock")
    with lock:
        path = get_idempotency_file_path()
        data = read_json(path, default={"version": 1, "keys": {}})
        keys = data.get("keys", {})
        candidate = keys.get(key)
        if candidate and isinstance(candidate, str):
            try:
                run_dir = get_run_dir(candidate)
                if (run_dir / "state.json").exists():
                    return candidate
            except (ValueError, TypeError):
                pass
        return None


def record_idempotency_key(key: str, run_id: str) -> None:
    """Record mapping from idempotency key to run ID."""
    if not key:
        return
    lock = get_lock(get_state_dir() / ".idempotency.lock")
    with lock:
        path = get_idempotency_file_path()

        def _update(data: Any) -> Any:
            keys = data.setdefault("keys", {})
            keys[key] = run_id
            return data

        update_json(path, _update, default={"version": 1, "keys": {}})


def create_run(
    prompt: str,
    *,
    workspace: str | None = None,
    profile: str | None = None,
    model: str | None = None,
    agent: str | None = None,
    effort: str | None = None,
    mode: str | None = None,
    timeout: float | None = None,
    sandbox: bool | None = None,
    additional_dirs: list[str] | None = None,
    auto_approval: bool = True,
    idempotency_key: str | None = None,
) -> tuple[RunRequest, RunState, bool]:
    """Persist run request and initial state before spawning worker.

    Returns:
        (request, state, is_new)
    """
    if idempotency_key:
        existing_id = find_run_by_idempotency_key(idempotency_key)
        if existing_id:
            req = get_run_request(existing_id)
            state = get_run_state(existing_id)
            return req, state, False

    run_id = f"run_{uuid.uuid4().hex[:16]}"
    run_dir = get_run_dir(run_id)
    ensure_private_directory(run_dir)

    stdout_path = str(run_dir / "stdout.log")
    stderr_path = str(run_dir / "stderr.log")
    log_path = str(run_dir / "agy.log")

    for p in (stdout_path, stderr_path, log_path):
        p_path = Path(p)
        if not p_path.exists():
            p_path.touch(mode=0o600)
        ensure_private_file(p_path)

    req = RunRequest(
        run_id=run_id,
        prompt=prompt,
        workspace=workspace,
        profile=profile,
        model=model,
        agent=agent,
        effort=effort,
        mode=mode,
        timeout=timeout,
        sandbox=sandbox,
        additional_dirs=additional_dirs,
        auto_approval=auto_approval,
        idempotency_key=idempotency_key,
    )

    state = RunState(
        run_id=run_id,
        status="queued",
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        log_path=log_path,
    )

    atomic_write_json(run_dir / "request.json", req.to_dict())
    atomic_write_json(run_dir / "state.json", state.to_dict())

    if idempotency_key:
        record_idempotency_key(idempotency_key, run_id)

    return req, state, True


def spawn_detached_worker(run_id: str) -> subprocess.Popen[Any]:
    """Spawn worker process detached from the current session/group."""
    cmd = [sys.executable, "-m", "magy.worker", run_id]

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
    }

    if os.name == "nt":
        creationflags = 0
        if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
            creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
        if hasattr(subprocess, "DETACHED_PROCESS"):
            creationflags |= subprocess.DETACHED_PROCESS
        popen_kwargs["creationflags"] = creationflags
    else:
        popen_kwargs["start_new_session"] = True

    return subprocess.Popen(cmd, **popen_kwargs)


def start_run(
    prompt: str,
    *,
    workspace: str | None = None,
    profile: str | None = None,
    model: str | None = None,
    agent: str | None = None,
    effort: str | None = None,
    mode: str | None = None,
    timeout: float | None = None,
    sandbox: bool | None = None,
    additional_dirs: list[str] | None = None,
    auto_approval: bool = True,
    idempotency_key: str | None = None,
) -> RunStatus:
    """Create a run and spawn its detached worker (or return existing if idempotent)."""
    req, state, is_new = create_run(
        prompt=prompt,
        workspace=workspace,
        profile=profile,
        model=model,
        agent=agent,
        effort=effort,
        mode=mode,
        timeout=timeout,
        sandbox=sandbox,
        additional_dirs=additional_dirs,
        auto_approval=auto_approval,
        idempotency_key=idempotency_key,
    )

    if is_new:
        spawn_detached_worker(req.run_id)

    return get_run_status(req.run_id)


def get_run_request(run_id: str) -> RunRequest:
    """Read persisted RunRequest from run directory."""
    run_dir = get_run_dir(run_id)
    path = run_dir / "request.json"
    data = read_json(path)
    if data is None:
        raise FileNotFoundError(f"Run request not found for '{run_id}'")
    return RunRequest.from_dict(data)


def get_run_state(run_id: str) -> RunState:
    """Read persisted RunState from run directory."""
    run_dir = get_run_dir(run_id)
    path = run_dir / "state.json"
    data = read_json(path)
    if data is None:
        raise FileNotFoundError(f"Run state not found for '{run_id}'")
    return RunState.from_dict(data)


def update_run_state(run_id: str, updates: dict[str, Any]) -> RunState:
    """Update fields on RunState under lock."""
    lock = get_run_lock(run_id)
    with lock:
        run_dir = get_run_dir(run_id)
        path = run_dir / "state.json"
        data = read_json(path)
        if data is None:
            raise FileNotFoundError(f"Run state not found for '{run_id}'")
        data.update(updates)
        atomic_write_json(path, data)
        return RunState.from_dict(data)


def get_run_status(run_id: str) -> RunStatus:
    """Return caller-safe status omitting prompt, raw commands, and secret paths."""
    state = get_run_state(run_id)
    retryable = state.health_classification in ("quota-exhausted", "rate-limited")
    suggested_profile: str | None = None

    if retryable:
        try:
            profiles = load_profiles()
            for name, meta in profiles.items():
                if name != state.profile and meta.enabled and meta.is_available():
                    suggested_profile = name
                    break
        except Exception:
            pass

    return RunStatus(
        run_id=state.run_id,
        status=state.status,
        profile=state.profile,
        created_at=state.created_at,
        started_at=state.started_at,
        finished_at=state.finished_at,
        exit_code=state.exit_code,
        error=state.error,
        retryable=retryable,
        suggested_profile=suggested_profile,
    )


def get_run_result(
    run_id: str,
    offset: int = 0,
    limit: int = 65536,
) -> RunResult:
    """Retrieve bounded output chunk with stable byte offsets."""
    if offset < 0:
        raise ValueError(f"Offset cannot be negative: {offset}")
    if limit <= 0:
        raise ValueError(f"Limit must be greater than zero: {limit}")

    state = get_run_state(run_id)
    stdout_file = Path(state.stdout_path) if state.stdout_path else None

    content_str = ""
    next_offset = offset
    eof = state.status in TERMINAL_STATUSES
    is_json = False

    if stdout_file and stdout_file.exists():
        try:
            with open(stdout_file, "rb") as f:
                f.seek(0, os.SEEK_END)
                file_size = f.tell()

                if offset < file_size:
                    f.seek(offset)
                    raw = f.read(limit)
                    next_offset = offset + len(raw)
                    content_str = raw.decode("utf-8", errors="replace")
                else:
                    next_offset = offset

                eof = (next_offset >= file_size) and (state.status in TERMINAL_STATUSES)
        except OSError:
            pass

    # Check if content is valid JSON
    trimmed = content_str.strip()
    if (trimmed.startswith("{") and trimmed.endswith("}")) or (
        trimmed.startswith("[") and trimmed.endswith("]")
    ):
        try:
            json.loads(trimmed)
            is_json = True
        except ValueError:
            is_json = False

    return RunResult(
        run_id=state.run_id,
        status=state.status,
        exit_code=state.exit_code,
        content=content_str,
        offset=offset,
        next_offset=next_offset,
        eof=eof,
        is_json=is_json,
    )


def wait_run(
    run_id: str,
    timeout: float = 20.0,
    poll_interval: float = 0.25,
) -> RunStatus:
    """Short-poll run status until completion or timeout."""
    # Clamp timeout between 0.1 and 60.0 to stay well within MCP gateway limits
    clamped_timeout = max(0.1, min(float(timeout), 60.0))
    deadline = time.time() + clamped_timeout

    while time.time() < deadline:
        st = get_run_status(run_id)
        if st.status in TERMINAL_STATUSES:
            return st
        time.sleep(poll_interval)

    return get_run_status(run_id)


def _is_pid_alive(pid: int) -> bool:
    """Check if process is alive without signalling."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False


def _verify_proc_marker(pid: int, run_id: str) -> bool:
    """On Linux, verify process environment marker to avoid targeting reused PIDs."""
    if os.name != "posix" or not sys.platform.startswith("linux"):
        return True
    environ_path = Path(f"/proc/{pid}/environ")
    if not environ_path.exists():
        return False
    try:
        raw = environ_path.read_bytes()
        marker = f"MAGY_RUN_ID={run_id}".encode("utf-8")
        return marker in raw
    except OSError:
        return False


def _terminate_pid_tree(
    pid: int, pgid: int | None, run_id: str, grace_period: float = 1.0
) -> None:
    """Gracefully terminate a process tree, escalating to SIGKILL."""
    if not _is_pid_alive(pid):
        return

    if not _verify_proc_marker(pid, run_id):
        # PID might have been reused; do not kill
        return

    # 1. Send SIGTERM
    if os.name == "nt":
        try:
            subprocess.run(
                ["taskkill", "/F", "/T", "/PID", str(pid)],
                capture_output=True,
                check=False,
            )
        except OSError:
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
        return

    # POSIX: signal group if pgid available and matches
    if pgid is not None and pgid > 1:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            try:
                os.kill(pid, signal.SIGTERM)
            except OSError:
                pass
    else:
        try:
            os.kill(pid, signal.SIGTERM)
        except OSError:
            pass

    # Wait grace period
    deadline = time.time() + grace_period
    while time.time() < deadline:
        if not _is_pid_alive(pid):
            return
        time.sleep(0.1)

    # 2. Escalate to SIGKILL
    if pgid is not None and pgid > 1:
        try:
            os.killpg(pgid, signal.SIGKILL)
        except OSError:
            try:
                os.kill(pid, signal.SIGKILL)
            except OSError:
                pass
    else:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def cancel_run(run_id: str) -> RunStatus:
    """Cancel a running or queued run, killing its process tree."""
    lock = get_run_lock(run_id)
    with lock:
        state = get_run_state(run_id)
        if state.status in TERMINAL_STATUSES:
            return get_run_status(run_id)

        # Mark cancelled immediately
        now = time.time()
        state.status = "cancelled"
        state.finished_at = now
        state.error = "Run was cancelled by user"

        atomic_write_json(get_run_dir(run_id) / "state.json", state.to_dict())

    # Outside lock, terminate processes to avoid holding lock during grace period
    if state.child_pid:
        _terminate_pid_tree(state.child_pid, state.child_pgid, run_id, grace_period=1.0)

    if state.worker_pid and _is_pid_alive(state.worker_pid):
        try:
            os.kill(state.worker_pid, signal.SIGTERM)
        except OSError:
            pass

    return get_run_status(run_id)
