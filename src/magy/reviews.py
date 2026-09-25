import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from filelock import FileLock

from magy.agy import get_agy_capabilities, resolve_agy_executable
from magy.config import get_state_dir, load_config_result
from magy.profiles import update_profile_health
from magy.routing import select_profile
from magy.runs import (
    MAX_RESULT_CHUNK_BYTES,
    MIN_RESULT_CHUNK_BYTES,
    _terminate_pid_tree,
)
from magy.storage import (
    atomic_write_json,
    ensure_private_directory,
    ensure_private_file,
    get_lock,
    read_json,
    validate_run_id,
)

TERMINAL_REVIEW_STATUSES = frozenset({"completed", "failed", "cancelled"})


@dataclass
class ReviewRunRequest:
    review_id: str
    prompt: str
    workspace: str
    repo_root: str
    profile: str
    model: str | None = None
    agent: str | None = None
    effort: str | None = None
    mode: str | None = None
    sandbox: bool | None = None
    additional_dirs: list[str] | None = None
    auto_approval: bool = False
    continue_review_id: str | None = None
    conversation_id: str | None = None
    baseline_tree: str = ""
    baseline_commit: str = ""
    created_at: float = field(default_factory=time.time)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewRunRequest":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ReviewRunState:
    review_id: str
    status: str = "running"
    profile: str = ""
    workspace: str = ""
    repo_root: str = ""
    pane_id: str = ""
    session: str = ""
    baseline_tree: str = ""
    baseline_commit: str = ""
    final_tree: str | None = None
    runner_pid: int | None = None
    runner_create_time: float | None = None
    exit_code: int | None = None
    error: str | None = None
    created_at: float = field(default_factory=time.time)
    started_at: float | None = None
    finished_at: float | None = None
    pty_log_path: str = ""
    diff_path: str = ""
    exit_signal_path: str = ""
    conversation_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ReviewRunState":
        return cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})


@dataclass
class ReviewRunStart:
    review_id: str
    pane_id: str
    profile: str
    workspace: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewRunStatus:
    review_id: str
    status: str
    profile: str | None = None
    workspace: str | None = None
    conversation_id: str | None = None
    exit_code: int | None = None
    error: str | None = None
    created_at: float = 0.0
    started_at: float | None = None
    finished_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewLogResult:
    review_id: str
    status: str
    content: str = ""
    offset: int = 0
    next_offset: int = 0
    eof: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class ReviewDiffResult:
    review_id: str
    status: str
    exit_code: int | None = None
    diff: str = ""
    offset: int = 0
    next_offset: int = 0
    eof: bool = False

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def validate_review_id(review_id: str) -> str:
    """Validate review identifier format and safety."""
    return validate_run_id(review_id)


def get_reviews_dir() -> Path:
    """Return root directory for review runs."""
    d = get_state_dir() / "reviews"
    ensure_private_directory(d)
    return d


def get_review_dir(review_id: str) -> Path:
    """Return validated directory path for a review run."""
    validated = validate_review_id(review_id)
    return get_reviews_dir() / validated


def get_review_lock(review_id: str) -> FileLock:
    """Return FileLock for review run mutations."""
    review_dir = get_review_dir(review_id)
    ensure_private_directory(review_dir)
    return get_lock(review_dir / ".review.lock")


def get_repo_leases_dir() -> Path:
    """Return directory for repository exclusion leases."""
    d = get_state_dir() / "repo_leases"
    ensure_private_directory(d)
    return d


def _repo_lease_path(repo_root: Path) -> Path:
    canonical = str(repo_root.resolve())
    key = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
    return get_repo_leases_dir() / f"{key}.json"


def acquire_repo_lease(repo_root: Path, review_id: str) -> None:
    """Acquire exclusive lease for a repository root among active review runs."""
    lease_file = _repo_lease_path(repo_root)
    lock = get_lock(lease_file.with_suffix(".lock"))
    with lock:
        if lease_file.exists():
            try:
                data = read_json(lease_file)
                if data and isinstance(data, dict):
                    active_id = data.get("review_id")
                    if active_id and active_id != review_id:
                        try:
                            prior_state = get_review_state(active_id)
                            if prior_state.status == "running":
                                raise ValueError(
                                    "Another reviewed run is already active for this "
                                    "repository"
                                )
                        except (FileNotFoundError, ValueError) as exc:
                            if "Another reviewed run" in str(exc):
                                raise
            except ValueError:
                raise
            except Exception:
                pass

        canonical = str(repo_root.resolve())
        atomic_write_json(
            lease_file,
            {
                "review_id": review_id,
                "repo_root": canonical,
                "acquired_at": time.time(),
            },
        )


def release_repo_lease(repo_root: Path, review_id: str) -> None:
    """Release exclusive lease for a repository root if owned by review_id."""
    lease_file = _repo_lease_path(repo_root)
    lock = get_lock(lease_file.with_suffix(".lock"))
    with lock:
        if lease_file.exists():
            try:
                data = read_json(lease_file)
                if data and data.get("review_id") == review_id:
                    lease_file.unlink(missing_ok=True)
            except Exception:
                pass


def validate_git_repository(workspace_path: Path) -> tuple[Path, str]:
    """Validate workspace is within a git repository with a HEAD commit."""
    git_bin = shutil.which("git")
    if git_bin is None:
        raise RuntimeError("Git executable is unavailable on PATH")

    try:
        res_root = subprocess.run(
            [git_bin, "rev-parse", "--show-toplevel"],
            cwd=workspace_path,
            capture_output=True,
            text=True,
            check=False,
        )
    except OSError as exc:
        raise RuntimeError("Git executable could not be run") from exc

    if res_root.returncode != 0:
        raise ValueError("Workspace is not inside a git repository")

    repo_root = Path(res_root.stdout.strip()).resolve(strict=True)

    res_head = subprocess.run(
        [git_bin, "rev-parse", "--verify", "HEAD"],
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if res_head.returncode != 0:
        raise ValueError("Git repository does not have a HEAD commit")

    return repo_root, res_head.stdout.strip()


def take_git_snapshot(repo_root: Path, temp_index_path: Path) -> str:
    """Snapshot repository content into a git tree."""
    git_bin = shutil.which("git") or "git"
    env = {**os.environ, "GIT_INDEX_FILE": str(temp_index_path)}

    try:
        # Read HEAD into temporary index
        res1 = subprocess.run(
            [git_bin, "read-tree", "HEAD"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if res1.returncode != 0:
            raise RuntimeError(f"git read-tree failed: {res1.stderr.strip()}")

        # Add all working tree changes (-A stages modifications, deletions, untracked)
        res2 = subprocess.run(
            [git_bin, "add", "-A"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if res2.returncode != 0:
            raise RuntimeError(f"git add -A failed: {res2.stderr.strip()}")

        # Write tree from temporary index
        res3 = subprocess.run(
            [git_bin, "write-tree"],
            cwd=repo_root,
            env=env,
            capture_output=True,
            text=True,
            check=False,
        )
        if res3.returncode != 0:
            raise RuntimeError(f"git write-tree failed: {res3.stderr.strip()}")

        return res3.stdout.strip()
    finally:
        if temp_index_path.exists():
            try:
                temp_index_path.unlink()
            except OSError:
                pass


def compute_git_diff(repo_root: Path, baseline_tree: str, final_tree: str) -> str:
    """Generate patch diff between baseline and final trees for repository."""
    git_bin = shutil.which("git") or "git"
    cmd = [
        git_bin,
        "diff",
        "--no-ext-diff",
        "--no-textconv",
        "--binary",
        baseline_tree,
        final_tree,
    ]
    res = subprocess.run(
        cmd,
        cwd=repo_root,
        capture_output=True,
        text=True,
        check=False,
    )
    if res.returncode != 0:
        raise RuntimeError(f"git diff failed: {res.stderr.strip()}")
    return res.stdout


def validate_zellij_environment() -> str:
    """Verify Zellij executable and active session environment."""
    zellij = shutil.which("zellij")
    if zellij is None:
        raise RuntimeError(
            "Reviewed mode requires Zellij installed and available on PATH"
        )
    if not os.environ.get("ZELLIJ") or not os.environ.get("ZELLIJ_SESSION_NAME"):
        raise RuntimeError(
            "Reviewed mode requires Magy MCP to run inside an active Zellij session; "
            "start OpenCode from Zellij and reconnect its MCP server"
        )
    return zellij


def is_pane_alive(pane_id: str) -> bool:
    """Check if a Zellij pane is open and active."""
    if not pane_id:
        return False
    zellij = shutil.which("zellij")
    if not zellij:
        return False
    raw_id = pane_id.removeprefix("terminal_")
    try:
        res = subprocess.run(
            [zellij, "action", "list-panes", "--json"],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
        if res.returncode != 0:
            return True
        panes = json.loads(res.stdout)
        for p in panes:
            if str(p.get("id")) == raw_id and not p.get("is_plugin", False):
                if p.get("exited", False):
                    return False
                return True
        return False
    except Exception:
        return True


def close_zellij_pane(pane_id: str) -> None:
    """Close Zellij pane by ID."""
    if not pane_id:
        return
    zellij = shutil.which("zellij")
    if not zellij:
        return
    raw_id = pane_id.removeprefix("terminal_")
    try:
        subprocess.run(
            [zellij, "action", "close-pane", "--pane-id", raw_id],
            capture_output=True,
            text=True,
            check=False,
            timeout=5,
        )
    except Exception:
        pass


def get_review_request(review_id: str) -> ReviewRunRequest:
    """Read persisted ReviewRunRequest."""
    review_dir = get_review_dir(review_id)
    data = read_json(review_dir / "request.json")
    if data is None:
        raise FileNotFoundError(f"Review request not found for '{review_id}'")
    return ReviewRunRequest.from_dict(data)


def get_review_state(review_id: str) -> ReviewRunState:
    """Read persisted ReviewRunState."""
    review_dir = get_review_dir(review_id)
    data = read_json(review_dir / "state.json")
    if data is None:
        raise FileNotFoundError(f"Review state not found for '{review_id}'")
    return ReviewRunState.from_dict(data)


def update_review_state(review_id: str, updates: dict[str, Any]) -> ReviewRunState:
    """Update fields on ReviewRunState under lock."""
    lock = get_review_lock(review_id)
    with lock:
        review_dir = get_review_dir(review_id)
        path = review_dir / "state.json"
        data = read_json(path)
        if data is None:
            raise FileNotFoundError(f"Review state not found for '{review_id}'")
        data.update(updates)
        atomic_write_json(path, data)
        return ReviewRunState.from_dict(data)


def spawn_detached_monitor(review_id: str) -> subprocess.Popen[Any]:
    """Spawn detached monitor process for review run completion."""
    cmd = [sys.executable, "-m", "magy.review_monitor", review_id]
    popen_kwargs: dict[str, Any] = {
        "stdin": subprocess.DEVNULL,
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
        "close_fds": True,
        "env": {**os.environ, "MAGY_REVIEW_ID": review_id},
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


def finalize_review(
    review_id: str,
    exit_code: int,
    *,
    error: str | None = None,
    status: str | None = None,
) -> ReviewRunState:
    """Finalize a review run, generating final snapshot diff and releasing lease."""
    lock = get_review_lock(review_id)
    with lock:
        curr = get_review_state(review_id)
        if curr.status in TERMINAL_REVIEW_STATUSES:
            return curr

        repo_root = Path(curr.repo_root)
        review_dir = get_review_dir(review_id)

        # Snapshot final tree and compute diff
        diff_content = ""
        snapshot_failed = False
        try:
            temp_final_index = review_dir / f"final_index_{uuid.uuid4().hex[:8]}"
            final_tree = take_git_snapshot(repo_root, temp_final_index)
            curr.final_tree = final_tree
            diff_content = compute_git_diff(repo_root, curr.baseline_tree, final_tree)
        except Exception as exc:
            snapshot_failed = True
            if error is None:
                error = f"Failed to compute final git snapshot: {exc}"

        diff_file = Path(curr.diff_path)
        diff_file.write_text(diff_content, encoding="utf-8")

        curr.exit_code = exit_code
        curr.finished_at = time.time()
        if snapshot_failed:
            # Cannot trust empty diff; always treat as failure regardless of exit_code
            curr.status = "failed"
            curr.error = error
            try:
                update_profile_health(curr.profile, "unknown-failure", is_success=False)
            except Exception:
                pass
        elif status is not None:
            curr.status = status
            curr.error = error
        elif exit_code == 0:
            curr.status = "completed"
            curr.error = None
            try:
                update_profile_health(curr.profile, "healthy", is_success=True)
            except Exception:
                pass
        else:
            curr.status = "failed"
            curr.error = error or f"Agy exited with code {exit_code}"
            try:
                update_profile_health(curr.profile, "unknown-failure", is_success=False)
            except Exception:
                pass

        atomic_write_json(review_dir / "state.json", curr.to_dict())
        release_repo_lease(repo_root, review_id)

    # Close pane after lock release
    if curr.pane_id:
        try:
            close_zellij_pane(curr.pane_id)
        except Exception:
            pass

    return curr


def run_review_monitor(
    review_id: str,
    poll_interval: float = 0.25,
    timeout: float = 86400.0,
) -> int:
    """Monitor loop polling for review exit signal or pane closure."""
    review_dir = get_review_dir(review_id)
    exit_file = review_dir / ".exit"
    deadline = time.time() + timeout

    while time.time() < deadline:
        try:
            curr = get_review_state(review_id)
        except Exception:
            return 1

        if curr.status in TERMINAL_REVIEW_STATUSES:
            return 0

        if exit_file.exists():
            try:
                code = int(exit_file.read_text(encoding="utf-8").strip())
            except Exception:
                finalize_review(
                    review_id,
                    exit_code=1,
                    error="Exit signal file is corrupted or unreadable",
                    status="failed",
                )
                return 1
            finalize_review(review_id, exit_code=code)
            return 0

        if not is_pane_alive(curr.pane_id):
            if exit_file.exists():
                try:
                    code = int(exit_file.read_text(encoding="utf-8").strip())
                except Exception:
                    finalize_review(
                        review_id,
                        exit_code=1,
                        error="Exit signal file is corrupted or unreadable",
                        status="failed",
                    )
                    return 1
                finalize_review(review_id, exit_code=code)
            else:
                finalize_review(
                    review_id,
                    exit_code=1,
                    error="Pane closed before execution completed",
                    status="failed",
                )
            return 0

        time.sleep(poll_interval)
    return 0


def start_review_run(
    prompt: str,
    *,
    workspace: str | None = None,
    profile: str | None = None,
    model: str | None = None,
    agent: str | None = None,
    effort: str | None = None,
    mode: str | None = None,
    sandbox: bool | None = None,
    additional_dirs: list[str] | None = None,
    auto_approval: bool = False,
    continue_review_id: str | None = None,
) -> ReviewRunStart:
    """Start reviewed Agy run in floating Zellij pane with git snapshot baseline."""
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("Prompt cannot be empty")
    if additional_dirs is not None and any(
        not isinstance(d, str) or not d.strip() for d in additional_dirs
    ):
        raise ValueError("Additional directories must be non-empty strings")

    zellij_bin = validate_zellij_environment()

    ws = Path(workspace).expanduser() if workspace else Path.cwd()
    try:
        workspace_path = ws.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Workspace directory does not exist") from exc
    if not workspace_path.is_dir():
        raise ValueError("Workspace must be a directory")

    repo_root, head_commit = validate_git_repository(workspace_path)

    # Profile pinning and continuation
    selected_profile_name = profile
    conversation_id_to_continue = None
    if continue_review_id:
        prior_dir = get_review_dir(continue_review_id)
        if not (prior_dir / "state.json").exists():
            raise ValueError(f"Prior review '{continue_review_id}' does not exist")
        prior_state = get_review_state(continue_review_id)
        if profile is not None and profile != prior_state.profile:
            raise ValueError(
                f"Cannot specify profile '{profile}' when continuing review "
                f"with profile '{prior_state.profile}'"
            )
        selected_profile_name = prior_state.profile
        conversation_id_to_continue = prior_state.conversation_id
        if not conversation_id_to_continue and (prior_dir / "pty.log").exists():
            try:
                with open(prior_dir / "pty.log", "r", encoding="utf-8") as f:
                    for _line in f:
                        _line = _line.strip()
                        if _line.startswith("{"):
                            _d = json.loads(_line)
                            _c = _d.get("conversation_id") or _d.get("init", {}).get(
                                "conversation_id"
                            )
                            if _c:
                                conversation_id_to_continue = _c
                                break
            except Exception:
                pass

    selected = select_profile(explicit_name=selected_profile_name)
    profile_name = selected.name

    # Validate Agy capabilities
    config_result = load_config_result()
    if config_result.error:
        raise ValueError("Magy configuration is invalid; run magy doctor")
    executable, _ = resolve_agy_executable(
        configured_cmd=config_result.config.agy_cmd,
        configured_resolver=config_result.config.agy_resolver,
        cwd=workspace_path,
    )
    if executable is None:
        raise RuntimeError("Agy executable is unavailable; run magy doctor")
    caps = get_agy_capabilities(executable)
    if auto_approval and not caps.get("supports_auto_approval", False):
        raise RuntimeError(
            "Installed Agy does not support auto-approval; update Agy or "
            "set auto_approval=False"
        )
    if additional_dirs and not caps.get("supports_add_dir", False):
        raise RuntimeError("Installed Agy does not support additional directories")

    # Generate review ID and prepare review directory
    review_id = f"rev_{uuid.uuid4().hex[:16]}"
    review_dir = get_review_dir(review_id)
    ensure_private_directory(review_dir)

    # Write a minimal state stub BEFORE acquiring the lease so that any
    # concurrent runner that reads the lease file and checks this review's
    # state.json will see status="running" and back off.
    _stub_state = {
        "review_id": review_id,
        "status": "running",
        "profile": profile_name,
        "workspace": str(workspace_path),
        "repo_root": str(repo_root),
        "session": os.environ.get("ZELLIJ_SESSION_NAME", ""),
        "baseline_tree": None,
        "baseline_commit": head_commit,
        "final_tree": None,
        "started_at": time.time(),
        "created_at": time.time(),
        "finished_at": None,
        "exit_code": None,
        "error": None,
        "pane_id": None,
        "runner_pid": None,
        "runner_create_time": None,
        "pty_log_path": None,
        "diff_path": None,
        "exit_signal_path": None,
        "conversation_id": conversation_id_to_continue,
    }
    atomic_write_json(review_dir / "state.json", _stub_state)

    acquire_repo_lease(repo_root, review_id)

    try:
        # Capture baseline tree snapshot
        temp_baseline_index = review_dir / f"baseline_index_{uuid.uuid4().hex[:8]}"
        baseline_tree = take_git_snapshot(repo_root, temp_baseline_index)

        pty_log = review_dir / "pty.log"
        pty_log.touch(mode=0o600)
        ensure_private_file(pty_log)

        diff_file = review_dir / "diff.patch"
        diff_file.touch(mode=0o600)
        ensure_private_file(diff_file)

        exit_file = review_dir / ".exit"

        req = ReviewRunRequest(
            review_id=review_id,
            prompt=prompt,
            workspace=str(workspace_path),
            repo_root=str(repo_root),
            profile=profile_name,
            model=model,
            agent=agent,
            effort=effort,
            mode=mode,
            sandbox=sandbox,
            additional_dirs=additional_dirs,
            auto_approval=auto_approval,
            continue_review_id=continue_review_id,
            conversation_id=conversation_id_to_continue,
            baseline_tree=baseline_tree,
            baseline_commit=head_commit,
        )

        state = ReviewRunState(
            review_id=review_id,
            status="running",
            profile=profile_name,
            workspace=str(workspace_path),
            repo_root=str(repo_root),
            session=os.environ.get("ZELLIJ_SESSION_NAME", ""),
            baseline_tree=baseline_tree,
            baseline_commit=head_commit,
            started_at=time.time(),
            pty_log_path=str(pty_log),
            diff_path=str(diff_file),
            exit_signal_path=str(exit_file),
            conversation_id=conversation_id_to_continue,
        )

        atomic_write_json(review_dir / "request.json", req.to_dict())
        atomic_write_json(review_dir / "state.json", state.to_dict())

        # Generate owner-only bash script
        runner_sh = review_dir / "runner.sh"
        script_content = (
            "#!/usr/bin/env bash\n"
            "set -u\n"
            f'REVIEW_DIR="{review_dir}"\n'
            'EXIT_FILE="$REVIEW_DIR/.exit"\n'
            'EXIT_TMP="$REVIEW_DIR/.exit.tmp.$$"\n'
            f'"{sys.executable}" -m magy.review_runner "$REVIEW_DIR"\n'
            "EXIT_CODE=$?\n"
            'echo "$EXIT_CODE" > "$EXIT_TMP"\n'
            'mv -f "$EXIT_TMP" "$EXIT_FILE"\n'
            'exit "$EXIT_CODE"\n'
        )
        runner_sh.write_text(script_content, encoding="utf-8")
        runner_sh.chmod(0o700)

        # Launch floating pane in Zellij
        command = [
            zellij_bin,
            "run",
            "--floating",
            "--name",
            f"magy-task-{review_id}",
            "--cwd",
            str(workspace_path),
            "--",
            "bash",
            "-c",
            'exec bash "$1"',
            "magy-review",
            str(runner_sh),
        ]

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                check=False,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RuntimeError("Zellij could not create a reviewed Magy pane") from exc

        if result.returncode != 0:
            raise RuntimeError(
                "Zellij could not create a reviewed Magy pane; verify the "
                "active session and available pane space"
            )

        pane_lines = result.stdout.strip().splitlines()
        if not pane_lines:
            raise RuntimeError("Zellij created no pane for the reviewed Magy run")

        pane_id = pane_lines[-1].strip()
        update_review_state(review_id, {"pane_id": pane_id})

        spawn_detached_monitor(review_id)

        return ReviewRunStart(
            review_id=review_id,
            pane_id=pane_id,
            profile=profile_name,
            workspace=str(workspace_path),
        )

    except Exception:
        release_repo_lease(repo_root, review_id)
        try:
            curr = get_review_state(review_id)
            if curr.status not in TERMINAL_REVIEW_STATUSES:
                curr.status = "failed"
                curr.error = "Review run could not be started"
                curr.finished_at = time.time()
                atomic_write_json(review_dir / "state.json", curr.to_dict())
        except Exception:
            pass
        raise


def get_review_status(review_id: str) -> ReviewRunStatus:
    """Return status of review run, reconciling with monitor exit signal on demand."""
    state = get_review_state(review_id)
    if state.status == "running":
        review_dir = get_review_dir(review_id)
        exit_file = review_dir / ".exit"
        if exit_file.exists():
            try:
                code = int(exit_file.read_text(encoding="utf-8").strip())
            except Exception:
                state = finalize_review(
                    review_id,
                    exit_code=1,
                    error="Exit signal file is corrupted or unreadable",
                    status="failed",
                )
                code = None  # already finalized
            if code is not None:
                state = finalize_review(review_id, exit_code=code)
        elif not is_pane_alive(state.pane_id):
            state = finalize_review(
                review_id,
                exit_code=1,
                error="Pane closed before execution completed",
                status="failed",
            )

    return ReviewRunStatus(
        review_id=state.review_id,
        status=state.status,
        profile=state.profile,
        workspace=state.workspace,
        conversation_id=state.conversation_id,
        exit_code=state.exit_code,
        error=state.error,
        created_at=state.created_at,
        started_at=state.started_at,
        finished_at=state.finished_at,
    )


def wait_review_run(
    review_id: str,
    timeout: float = 20.0,
    poll_interval: float = 0.25,
) -> ReviewRunStatus:
    """Short-poll review run status until completion or timeout."""
    clamped_timeout = max(0.1, min(float(timeout), 60.0))
    deadline = time.time() + clamped_timeout

    while time.time() < deadline:
        st = get_review_status(review_id)
        if st.status in TERMINAL_REVIEW_STATUSES:
            return st
        time.sleep(poll_interval)

    return get_review_status(review_id)


def cancel_review_run(review_id: str) -> ReviewRunStatus:
    """Cancel active review run, killing process tree, pane, and releasing lease."""
    lock = get_review_lock(review_id)
    with lock:
        state = get_review_state(review_id)
        if state.status in TERMINAL_REVIEW_STATUSES:
            return get_review_status(review_id)
        state.status = "cancelled"
        state.finished_at = time.time()
        state.error = "Review was cancelled by user"
        atomic_write_json(get_review_dir(review_id) / "state.json", state.to_dict())

    if state.repo_root:
        release_repo_lease(Path(state.repo_root), review_id)

    if state.runner_pid:
        try:
            _terminate_pid_tree(state.runner_pid, state.runner_create_time, review_id)
        except Exception:
            pass

    if state.pane_id:
        try:
            close_zellij_pane(state.pane_id)
        except Exception:
            pass

    return get_review_status(review_id)


def _read_file_chunk(
    path: Path,
    offset: int,
    limit: int,
    *,
    is_terminal: bool,
) -> tuple[str, int, bool]:
    """Read a bounded UTF-8 chunk from a file with stable byte offsets."""
    if not path.is_file():
        raise FileNotFoundError(f"File not found: {path}")

    content_str = ""
    next_offset = offset
    eof = is_terminal

    try:
        with open(path, "rb") as f:
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
                                    "Output ended with an incomplete UTF-8 character"
                                ) from e
                            raise ValueError("Output is not valid UTF-8") from e
                next_offset = offset + len(raw)
            else:
                next_offset = offset

            eof = (next_offset >= file_size) and is_terminal
    except OSError as e:
        raise OSError(f"Could not read {path}") from e

    return content_str, next_offset, eof


def get_review_log(
    review_id: str,
    offset: int = 0,
    limit: int = 65536,
) -> ReviewLogResult:
    """Retrieve bounded chunk from review PTY log."""
    if offset < 0:
        raise ValueError(f"Offset cannot be negative: {offset}")
    if limit < MIN_RESULT_CHUNK_BYTES:
        raise ValueError(
            f"Limit must be at least {MIN_RESULT_CHUNK_BYTES} bytes: {limit}"
        )
    if limit > MAX_RESULT_CHUNK_BYTES:
        raise ValueError(f"Limit cannot exceed {MAX_RESULT_CHUNK_BYTES} bytes: {limit}")

    state = get_review_state(review_id)
    log_path = (
        Path(state.pty_log_path)
        if state.pty_log_path
        else (get_review_dir(review_id) / "pty.log")
    )
    if not log_path.exists():
        raise FileNotFoundError("Review log is unavailable")

    content, next_offset, eof = _read_file_chunk(
        log_path,
        offset,
        limit,
        is_terminal=(state.status in TERMINAL_REVIEW_STATUSES),
    )
    return ReviewLogResult(
        review_id=state.review_id,
        status=state.status,
        content=content,
        offset=offset,
        next_offset=next_offset,
        eof=eof,
    )


def get_review_result(
    review_id: str,
    offset: int = 0,
    limit: int = 65536,
) -> ReviewDiffResult:
    """Retrieve bounded chunk of final Git diff after review completion."""
    if offset < 0:
        raise ValueError(f"Offset cannot be negative: {offset}")
    if limit < MIN_RESULT_CHUNK_BYTES:
        raise ValueError(
            f"Limit must be at least {MIN_RESULT_CHUNK_BYTES} bytes: {limit}"
        )
    if limit > MAX_RESULT_CHUNK_BYTES:
        raise ValueError(f"Limit cannot exceed {MAX_RESULT_CHUNK_BYTES} bytes: {limit}")

    state = get_review_state(review_id)
    diff_path = (
        Path(state.diff_path)
        if state.diff_path
        else (get_review_dir(review_id) / "diff.patch")
    )
    if not diff_path.exists():
        if state.status not in TERMINAL_REVIEW_STATUSES:
            return ReviewDiffResult(
                review_id=state.review_id,
                status=state.status,
                exit_code=state.exit_code,
                diff="",
                offset=0,
                next_offset=0,
                eof=False,
            )
        return ReviewDiffResult(
            review_id=state.review_id,
            status=state.status,
            exit_code=state.exit_code,
            diff="",
            offset=0,
            next_offset=0,
            eof=True,
        )

    content, next_offset, eof = _read_file_chunk(
        diff_path,
        offset,
        limit,
        is_terminal=(state.status in TERMINAL_REVIEW_STATUSES),
    )
    return ReviewDiffResult(
        review_id=state.review_id,
        status=state.status,
        exit_code=state.exit_code,
        diff=content,
        offset=offset,
        next_offset=next_offset,
        eof=eof,
    )
