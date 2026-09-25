import os
import subprocess
import sys
from pathlib import Path
from typing import Any

from magy.reviews import get_review_lock
from magy.runs import _process_create_time
from magy.storage import atomic_write_json, read_json


def run_review_runner(review_dir_path: str) -> int:
    """Execute Agy non-interactively with a PTY transcript in the active pane."""
    review_dir = Path(review_dir_path)
    req_file = review_dir / "request.json"
    req = read_json(req_file)
    if req is None:
        print(f"Error: request.json not found in {review_dir}", file=sys.stderr)
        return 1

    # Record runner PID in state.json under lock
    lock = get_review_lock(req["review_id"])
    with lock:
        state_file = review_dir / "state.json"
        state_data = read_json(state_file)
        if state_data:
            state_data["runner_pid"] = os.getpid()
            state_data["runner_create_time"] = _process_create_time(os.getpid())
            atomic_write_json(state_file, state_data)

    cmd = [
        sys.executable,
        "-m",
        "magy.cli",
        "--profile",
        req["profile"],
        "--",
        "--print",
        req["prompt"],
    ]
    if req.get("continue_review_id"):
        cmd.append("--continue")
    if req.get("auto_approval"):
        cmd.append("--dangerously-skip-permissions")
    if req.get("model"):
        cmd.extend(["--model", req["model"]])
    if req.get("agent"):
        cmd.extend(["--agent", req["agent"]])
    if req.get("effort"):
        cmd.extend(["--effort", req["effort"]])
    if req.get("mode"):
        cmd.extend(["--mode", req["mode"]])
    if req.get("sandbox"):
        cmd.append("--sandbox")
    for d in req.get("additional_dirs") or []:
        cmd.extend(["--add-dir", d])

    log_path = review_dir / "pty.log"
    if not log_path.exists():
        log_path.touch(mode=0o600)

    # Ensure working directory is workspace
    ws = req.get("workspace")
    if ws and Path(ws).is_dir():
        os.chdir(ws)

    os.environ["MAGY_REVIEW_ID"] = req["review_id"]
    # Also set MAGY_RUN_ID so _terminate_pid_tree can verify this process
    # during cancel_review_run (it checks MAGY_RUN_ID in process environment).
    os.environ["MAGY_RUN_ID"] = req["review_id"]

    try:
        import pty

        with open(log_path, "ab") as log_f:

            def master_read(fd: int) -> bytes:
                data = os.read(fd, 4096)
                if data:
                    log_f.write(data)
                    log_f.flush()
                return data

            status = pty.spawn(cmd, master_read=master_read)
            return os.waitstatus_to_exitcode(status)
    except (ImportError, AttributeError, OSError):
        # Fallback when pty is unavailable (e.g. non-POSIX or mock testing)
        with open(log_path, "ab") as log_f:
            proc: subprocess.Popen[Any] = subprocess.Popen(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            assert proc.stdout is not None
            while True:
                chunk = proc.stdout.read(1024)
                if not chunk:
                    break
                sys.stdout.buffer.write(chunk)
                sys.stdout.buffer.flush()
                log_f.write(chunk)
                log_f.flush()
            return proc.wait()


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for review runner process."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m magy.review_runner <review_dir>", file=sys.stderr)
        return 1
    return run_review_runner(args[0])


if __name__ == "__main__":
    sys.exit(main())
