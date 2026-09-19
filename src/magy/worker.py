import os
import subprocess
import sys
import time
from pathlib import Path
from typing import Any

from magy.agy import (
    classify_run_health,
    get_agy_capabilities,
    read_bounded_log_tail,
    resolve_agy_executable,
)
from magy.config import load_config_result
from magy.profiles import (
    acquire_profile_lease,
    build_profile_env,
    get_profile,
    sync_profile_settings,
    update_profile_health,
    validate_profile_name,
)
from magy.routing import select_profile
from magy.runs import (
    TERMINAL_STATUSES,
    _terminate_pid_tree,
    get_run_dir,
    get_run_lock,
    get_run_request,
    get_run_state,
    update_run_state,
)
from magy.storage import atomic_write_json


def run_worker(run_id: str) -> int:
    """Execute run workload in detached worker process."""
    try:
        req = get_run_request(run_id)
        state = get_run_state(run_id)
    except Exception as e:
        print(f"Worker failed to load run '{run_id}': {e}", file=sys.stderr)
        return 1

    lock = get_run_lock(run_id)
    with lock:
        # If already cancelled before worker started
        if state.status == "cancelled":
            return 0
        state.status = "running"
        state.started_at = time.time()
        state.worker_pid = os.getpid()
        atomic_write_json(get_run_dir(run_id) / "state.json", state.to_dict())

    cfg = load_config_result().config
    profile_name: str | None = None

    try:
        # 1. Profile selection
        if req.profile:
            p_name = validate_profile_name(req.profile)
            p_meta = get_profile(p_name)
            if p_meta is None:
                raise KeyError(f"Profile '{p_name}' does not exist")
            if not p_meta.enabled:
                raise ValueError(f"Profile '{p_name}' is disabled")
            profile_name = p_name
        else:
            sel = select_profile()
            profile_name = sel.name

        with acquire_profile_lease(profile_name, exclusive=False):
            prof_meta = get_profile(profile_name)
            incarnation_id = prof_meta.incarnation_id if prof_meta else None

            # 2. Settings synchronization & environment build
            sync_profile_settings(profile_name)
            child_env = build_profile_env(profile_name)
            child_env["MAGY_RUN_ID"] = run_id

            update_run_state(
                run_id,
                {
                    "profile": profile_name,
                    "profile_incarnation_id": incarnation_id,
                },
            )

            # 3. Executable resolution & capability detection
            ws_path = Path(req.workspace) if req.workspace else None
            exe, source = resolve_agy_executable(
                configured_cmd=cfg.agy_cmd,
                configured_resolver=cfg.agy_resolver,
                cwd=ws_path,
            )
            if exe is None:
                raise RuntimeError(f"Agy executable not found: {source}")

            caps = get_agy_capabilities(exe)

            # 4. Command building
            argv: list[str] = [str(exe), "--print", req.prompt]
            if caps.get("supports_json_output", False):
                argv.extend(["--output-format", "json"])
            if req.auto_approval:
                argv.append("--dangerously-skip-permissions")
            if req.additional_dirs:
                for d in req.additional_dirs:
                    argv.extend(["--add-dir", str(d)])
            if req.model:
                argv.extend(["--model", req.model])
            if req.agent:
                argv.extend(["--agent", req.agent])
            if req.effort:
                argv.extend(["--effort", req.effort])
            if req.mode:
                argv.extend(["--mode", req.mode])
            if req.sandbox:
                argv.append("--sandbox")

            # Dedicated private log file
            argv.extend(["--log-file", str(state.log_path)])

            # 5. Launch child process
            proc_kwargs: dict[str, Any] = {
                "cwd": req.workspace,
                "env": child_env,
                "stdin": subprocess.DEVNULL,
            }
            if os.name == "nt":
                proc_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
            else:
                proc_kwargs["process_group"] = 0

            with (
                open(state.stdout_path, "ab") as out_f,
                open(state.stderr_path, "ab") as err_f,
            ):
                proc = subprocess.Popen(argv, stdout=out_f, stderr=err_f, **proc_kwargs)

                child_pgid = os.getpgid(proc.pid) if os.name != "nt" else None
                update_run_state(
                    run_id,
                    {
                        "child_pid": proc.pid,
                        "child_pgid": child_pgid,
                    },
                )

                timed_out = False
                try:
                    if req.timeout is not None and req.timeout > 0:
                        proc.wait(timeout=req.timeout)
                    else:
                        proc.wait()
                except subprocess.TimeoutExpired:
                    timed_out = True
                    _terminate_pid_tree(proc.pid, child_pgid, run_id, grace_period=1.0)
                    try:
                        proc.wait(timeout=2.0)
                    except (subprocess.TimeoutExpired, OSError):
                        pass

            exit_code = proc.returncode

            # 6. Read stream tails & classify health
            stdout_tail = read_bounded_log_tail(state.stdout_path)
            stderr_tail = read_bounded_log_tail(state.stderr_path)
            log_tail = read_bounded_log_tail(state.log_path)

            with lock:
                curr_state = get_run_state(run_id)
                now = time.time()
                curr_state.finished_at = now
                curr_state.exit_code = exit_code

                if curr_state.status == "cancelled":
                    # Already cancelled, preserve status
                    atomic_write_json(
                        get_run_dir(run_id) / "state.json", curr_state.to_dict()
                    )
                    return 0

                if timed_out:
                    curr_state.status = "timed_out"
                    curr_state.error = f"Execution timed out after {req.timeout}s"
                    curr_state.health_classification = "timeout"
                    update_profile_health(
                        profile_name,
                        "timeout",
                        cooldown_seconds=cfg.cooldown_timeout,
                        reason="Request timed out",
                        is_success=False,
                    )
                elif exit_code == 0:
                    curr_state.status = "completed"
                    curr_state.error = None
                    update_profile_health(profile_name, "healthy", is_success=True)
                else:
                    classification = classify_run_health(
                        exit_code, stdout_tail, stderr_tail, log_tail, config=cfg
                    )
                    curr_state.status = "failed"
                    curr_state.error = classification.reason
                    curr_state.health_classification = classification.health
                    curr_state.cooldown_seconds = classification.cooldown_seconds
                    update_profile_health(
                        profile_name,
                        classification.health,
                        cooldown_seconds=classification.cooldown_seconds,
                        reason=classification.reason,
                        is_success=False,
                    )

                atomic_write_json(
                    get_run_dir(run_id) / "state.json", curr_state.to_dict()
                )
                return 0 if curr_state.status == "completed" else 1

    except Exception as e:
        with lock:
            curr_state = get_run_state(run_id)
            if curr_state.status not in TERMINAL_STATUSES:
                curr_state.status = "failed"
                curr_state.error = str(e)
                curr_state.finished_at = time.time()
                atomic_write_json(
                    get_run_dir(run_id) / "state.json", curr_state.to_dict()
                )
        return 1


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for detached worker execution."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m magy.worker <run_id>", file=sys.stderr)
        return 1
    run_id = args[0]
    return run_worker(run_id)


if __name__ == "__main__":
    sys.exit(main())
