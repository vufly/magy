import json
import math
import os
import subprocess
import sys
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

import psutil
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
MAX_RESULT_CHUNK_BYTES = 1024 * 1024
MIN_RESULT_CHUNK_BYTES = 4
WORKER_STARTUP_GRACE_SECONDS = 5.0


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
    worker_create_time: float | None = None
    child_pid: int | None = None
    child_create_time: float | None = None
    child_pgid: int | None = None
    exit_code: int | None = None
    error: str | None = None
    cooldown_seconds: float | None = None
    health_classification: str | None = None
    cleanup_pending: bool = False
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
    cleanup_pending: bool = False

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
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    if timeout is not None and (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(float(timeout))
        or timeout <= 0
    ):
        raise ValueError("Timeout must be a finite positive number")
    if idempotency_key is not None and (
        not isinstance(idempotency_key, str)
        or not idempotency_key.strip()
        or len(idempotency_key) > 256
    ):
        raise ValueError("Idempotency key must be 1 to 256 characters")
    if additional_dirs is not None and any(
        not isinstance(directory, str) or not directory.strip()
        for directory in additional_dirs
    ):
        raise ValueError("Additional directories must be non-empty strings")

    idempotency_lock = (
        get_lock(get_state_dir() / ".idempotency.lock") if idempotency_key else None
    )

    def _create_new() -> tuple[RunRequest, RunState]:
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
        return req, state

    if idempotency_key:
        assert idempotency_lock is not None
        with idempotency_lock:
            path = get_idempotency_file_path()
            data = read_json(path, default={"version": 1, "keys": {}})
            keys = data.setdefault("keys", {})
            existing_id = keys.get(idempotency_key)
            if isinstance(existing_id, str):
                try:
                    return (
                        get_run_request(existing_id),
                        get_run_state(existing_id),
                        False,
                    )
                except (FileNotFoundError, ValueError, TypeError):
                    keys.pop(idempotency_key, None)

            req, state = _create_new()
            keys[idempotency_key] = req.run_id
            atomic_write_json(path, data)
            return req, state, True

    req, state = _create_new()
    return req, state, True


def spawn_detached_worker(run_id: str) -> subprocess.Popen[Any]:
    """Spawn worker process detached from the current session/group."""
    cmd = [sys.executable, "-m", "magy.worker", run_id]

    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": {**os.environ, "MAGY_RUN_ID": run_id},
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
        proc: subprocess.Popen[Any] | None = None
        try:
            proc = spawn_detached_worker(req.run_id)
            publish_worker_process(req.run_id, proc.pid)
            threading.Thread(target=proc.wait, daemon=True).start()
        except Exception:
            worker_create_time = None
            cleanup_ok = True
            if proc is not None:
                worker_create_time = _process_create_time(proc.pid)
                try:
                    cleanup_ok = _terminate_pid_tree(
                        proc.pid, worker_create_time, req.run_id
                    )
                except Exception:
                    cleanup_ok = False
                if cleanup_ok:
                    try:
                        proc.wait(timeout=1.0)
                    except (subprocess.TimeoutExpired, OSError):
                        cleanup_ok = False
            fail_run_if_active(
                req.run_id,
                "Worker process could not be started",
                worker_pid=proc.pid if proc is not None else None,
                worker_create_time=worker_create_time,
                cleanup_pending=not cleanup_ok,
            )

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
    state = RunState.from_dict(data)
    state.error = _public_run_error(state.error)
    return state


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


def _process_create_time(pid: int) -> float | None:
    try:
        return psutil.Process(pid).create_time()
    except (psutil.Error, OSError):
        return None


def publish_worker_process(run_id: str, pid: int) -> RunState:
    """Publish detached worker identity without changing a terminal state."""
    lock = get_run_lock(run_id)
    with lock:
        state = get_run_state(run_id)
        if state.status in TERMINAL_STATUSES:
            return state
        state.worker_pid = pid
        state.worker_create_time = _process_create_time(pid)
        atomic_write_json(get_run_dir(run_id) / "state.json", state.to_dict())
        return state


def claim_run(run_id: str, worker_pid: int) -> tuple[RunState, bool]:
    """Atomically claim a queued run for one worker."""
    lock = get_run_lock(run_id)
    with lock:
        state = get_run_state(run_id)
        if state.status != "queued":
            return state, False
        state.status = "running"
        state.started_at = time.time()
        state.worker_pid = worker_pid
        state.worker_create_time = _process_create_time(worker_pid)
        atomic_write_json(get_run_dir(run_id) / "state.json", state.to_dict())
        return state, True


def fail_run_if_active(
    run_id: str,
    error: str,
    *,
    worker_pid: int | None = None,
    worker_create_time: float | None = None,
    cleanup_pending: bool = False,
) -> RunState:
    """Finalize a nonterminal run as failed without overwriting cancellation."""
    lock = get_run_lock(run_id)
    with lock:
        state = get_run_state(run_id)
        if state.status in TERMINAL_STATUSES | {"cancelling"}:
            return state
        state.status = "failed"
        state.error = _public_run_error(error)
        state.finished_at = time.time()
        if worker_pid is not None:
            state.worker_pid = worker_pid
            state.worker_create_time = worker_create_time
        state.cleanup_pending = cleanup_pending
        atomic_write_json(get_run_dir(run_id) / "state.json", state.to_dict())
        return state


def _public_run_error(error: str | None) -> str | None:
    if error is None:
        return None
    controlled = {
        "Worker process could not be started",
        "Worker process exited before finalizing the run",
        "Worker execution failed",
        "Magy configuration is invalid",
        "Run was cancelled by user",
        "Run was cancelled; process cleanup could not be verified",
        "Run failed",
    }
    if error in controlled:
        return error
    if error.startswith("Execution timed out after "):
        return error
    if error.startswith("Execution timed out; process cleanup"):
        return error
    return "Run failed"


def _cleanup_persisted_run_processes(state: RunState, run_id: str) -> bool:
    try:
        if state.child_pid is not None:
            return _terminate_pid_tree(
                state.child_pid,
                state.child_create_time,
                run_id,
            )
        if state.worker_pid is not None:
            return _terminate_pid_tree(
                state.worker_pid,
                state.worker_create_time,
                run_id,
            )
        return _terminate_run_marker_processes(run_id)
    except Exception:
        return False


def get_run_status(run_id: str) -> RunStatus:
    """Return caller-safe status omitting prompt, raw commands, and secret paths."""
    state = get_run_state(run_id)
    if state.status == "cancelling":
        cleanup_ok = _cleanup_persisted_run_processes(state, run_id)
        lock = get_run_lock(run_id)
        with lock:
            current = get_run_state(run_id)
            if current.status == "cancelling":
                current.status = "cancelled"
                current.finished_at = time.time()
                current.cleanup_pending = not cleanup_ok
                current.error = (
                    "Run was cancelled by user"
                    if cleanup_ok
                    else "Run was cancelled; process cleanup could not be verified"
                )
                atomic_write_json(get_run_dir(run_id) / "state.json", current.to_dict())
                state = current

    if state.cleanup_pending:
        cleanup_ok = _cleanup_persisted_run_processes(state, run_id)
        if cleanup_ok:
            lock = get_run_lock(run_id)
            with lock:
                current = get_run_state(run_id)
                if current.cleanup_pending:
                    current.cleanup_pending = False
                    if current.status == "cancelled":
                        current.error = "Run was cancelled by user"
                    elif current.status == "timed_out":
                        current.error = "Execution timed out"
                    atomic_write_json(
                        get_run_dir(run_id) / "state.json", current.to_dict()
                    )
                    state = current

    if state.status == "queued" and (
        time.time() - state.created_at > WORKER_STARTUP_GRACE_SECONDS
    ):
        cleanup_ok = _cleanup_persisted_run_processes(state, run_id)
        state = fail_run_if_active(
            run_id,
            "Worker process exited before finalizing the run",
            worker_pid=state.worker_pid,
            worker_create_time=state.worker_create_time,
            cleanup_pending=not cleanup_ok,
        )
    if (
        state.status in {"queued", "running"}
        and state.worker_pid is not None
        and not _process_identity_matches(
            state.worker_pid, state.worker_create_time, run_id
        )
    ):
        lock = get_run_lock(run_id)
        with lock:
            current = get_run_state(run_id)
            if (
                current.status in {"queued", "running"}
                and current.worker_pid is not None
                and current.worker_pid == state.worker_pid
                and not _process_identity_matches(
                    current.worker_pid,
                    current.worker_create_time,
                    run_id,
                )
            ):
                cleanup_ok = True
                if current.child_pid is not None:
                    try:
                        cleanup_ok = _terminate_pid_tree(
                            current.child_pid,
                            current.child_create_time,
                            run_id,
                        )
                    except Exception:
                        cleanup_ok = False
                else:
                    try:
                        cleanup_ok = _terminate_run_marker_processes(run_id)
                    except Exception:
                        cleanup_ok = False
                current.cleanup_pending = not cleanup_ok
                current.status = "failed"
                current.error = "Worker process exited before finalizing the run"
                current.finished_at = time.time()
                atomic_write_json(get_run_dir(run_id) / "state.json", current.to_dict())
                state = current
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
        cleanup_pending=state.cleanup_pending,
    )


def get_run_result(
    run_id: str,
    offset: int = 0,
    limit: int = 65536,
) -> RunResult:
    """Retrieve bounded output chunk with stable byte offsets."""
    if offset < 0:
        raise ValueError(f"Offset cannot be negative: {offset}")
    if limit < MIN_RESULT_CHUNK_BYTES:
        raise ValueError(
            f"Limit must be at least {MIN_RESULT_CHUNK_BYTES} bytes: {limit}"
        )
    if limit > MAX_RESULT_CHUNK_BYTES:
        raise ValueError(f"Limit cannot exceed {MAX_RESULT_CHUNK_BYTES} bytes: {limit}")

    state = get_run_state(run_id)
    stdout_file = Path(state.stdout_path) if state.stdout_path else None

    content_str = ""
    next_offset = offset
    eof = state.status in TERMINAL_STATUSES
    is_json = False

    if stdout_file is None or not stdout_file.is_file():
        raise FileNotFoundError("Run output is unavailable")
    try:
        with open(stdout_file, "rb") as f:
            f.seek(0, os.SEEK_END)
            file_size = f.tell()

            if offset < file_size:
                f.seek(offset)
                raw = f.read(limit)
                if raw:
                    while True:
                        try:
                            content_str = raw.decode("utf-8", errors="strict")
                            break
                        except UnicodeDecodeError as e:
                            if e.start == 0 and "unexpected end" not in e.reason:
                                raise ValueError(
                                    "Offset must point to a UTF-8 character boundary"
                                ) from e
                            if e.end == len(raw) and "unexpected end" in e.reason:
                                if e.start > 0:
                                    raw = raw[: e.start]
                                    content_str = raw.decode("utf-8")
                                    break
                                raise ValueError(
                                    "Run output ended with an incomplete UTF-8 "
                                    "character"
                                ) from e
                            raise ValueError("Run output is not valid UTF-8") from e
                next_offset = offset + len(raw)
            else:
                next_offset = offset

            eof = (next_offset >= file_size) and (state.status in TERMINAL_STATUSES)
    except OSError as e:
        raise OSError("Run output is unavailable") from e

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
    """Verify a process environment marker without trusting PID alone."""
    try:
        return psutil.Process(pid).environ().get("MAGY_RUN_ID") == run_id
    except (psutil.Error, OSError):
        return False


def _process_identity_matches(
    pid: int,
    create_time: float | None,
    run_id: str,
    *,
    require_marker: bool = False,
) -> bool:
    if pid <= 0 or create_time is None:
        return False
    try:
        proc = psutil.Process(pid)
        if proc.status() == psutil.STATUS_ZOMBIE:
            return False
        if abs(proc.create_time() - create_time) > 0.001:
            return False
    except (psutil.Error, OSError):
        return False
    if require_marker:
        return _verify_proc_marker(pid, run_id)
    return True


def _run_marker_processes(run_id: str) -> list[psutil.Process]:
    processes: list[psutil.Process] = []
    for proc in psutil.process_iter(["pid"]):
        try:
            if _verify_proc_marker(proc.pid, run_id):
                processes.append(proc)
        except (psutil.Error, OSError):
            continue
    return processes


def _terminate_run_marker_processes(run_id: str) -> bool:
    for process in _run_marker_processes(run_id):
        if process.pid == os.getpid():
            continue
        try:
            if process.status() == psutil.STATUS_ZOMBIE:
                continue
            return _terminate_pid_tree(
                process.pid,
                process.create_time(),
                run_id,
            )
        except (psutil.Error, OSError):
            continue
    return True


def _terminate_pid_tree(
    pid: int,
    create_time: float | None,
    run_id: str,
    grace_period: float = 1.0,
) -> bool:
    """Terminate one verified run process tree and marker-tagged descendants."""
    root_exists = psutil.pid_exists(pid)
    root_verified = _process_identity_matches(
        pid,
        create_time,
        run_id,
        require_marker=True,
    )
    marker_processes = _run_marker_processes(run_id)
    if not root_verified and not marker_processes:
        return not root_exists
    if root_exists and not root_verified and not marker_processes:
        return False

    known: dict[int, psutil.Process] = {}

    def _refresh() -> None:
        if root_verified and _process_identity_matches(
            pid,
            create_time,
            run_id,
            require_marker=True,
        ):
            try:
                root = psutil.Process(pid)
                known[root.pid] = root
                for child in root.children(recursive=True):
                    known[child.pid] = child
            except (psutil.Error, OSError):
                pass
        for process in _run_marker_processes(run_id):
            if process.pid != os.getpid():
                known[process.pid] = process

    def _alive() -> list[psutil.Process]:
        alive: list[psutil.Process] = []
        for process in known.values():
            try:
                if process.is_running() and process.status() != psutil.STATUS_ZOMBIE:
                    alive.append(process)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return alive

    _refresh()
    deadline = time.monotonic() + grace_period
    signalled: set[int] = set()
    while time.monotonic() < deadline:
        _refresh()
        alive = _alive()
        for process in alive:
            if process.pid in signalled:
                continue
            try:
                process.terminate()
                signalled.add(process.pid)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if not alive:
            return True
        time.sleep(0.05)

    kill_deadline = time.monotonic() + 1.0
    while time.monotonic() < kill_deadline:
        _refresh()
        alive = _alive()
        for process in alive:
            try:
                process.kill()
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        if not alive:
            return True
        time.sleep(0.05)
    _refresh()
    return not _alive()


def cancel_run(run_id: str) -> RunStatus:
    """Cancel a running or queued run, killing its process tree."""
    lock = get_run_lock(run_id)
    terminal = False
    with lock:
        state = get_run_state(run_id)
        if state.status in TERMINAL_STATUSES:
            terminal = True
        else:
            state.status = "cancelling"
            state.error = None
            atomic_write_json(get_run_dir(run_id) / "state.json", state.to_dict())

    if terminal:
        return get_run_status(run_id)

    cleanup_ok = _cleanup_persisted_run_processes(state, run_id)

    with lock:
        current = get_run_state(run_id)
        if current.status == "cancelling":
            current.status = "cancelled"
            current.finished_at = time.time()
            current.error = (
                "Run was cancelled by user"
                if cleanup_ok
                else "Run was cancelled; process cleanup could not be verified"
            )
            current.cleanup_pending = not cleanup_ok
            atomic_write_json(get_run_dir(run_id) / "state.json", current.to_dict())

    return get_run_status(run_id)
