import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

DEFAULT_RECORD_VARS = [
    "HOME",
    "USERPROFILE",
    "HOMEDRIVE",
    "HOMEPATH",
    "MAGY_REAL_HOME",
    "AGY_CLI_DISABLE_AUTO_UPDATE",
    "MAGY_PROFILE",
    "MAGY_AGY_CMD",
]


def record_invocation() -> None:
    log_file = os.environ.get("FAKE_AGY_LOG_FILE")
    if not log_file:
        return

    vars_to_record = os.environ.get("FAKE_AGY_RECORD_VARS")
    if vars_to_record:
        var_names = [v.strip() for v in vars_to_record.split(",") if v.strip()]
    else:
        var_names = DEFAULT_RECORD_VARS

    recorded_env = {var: os.environ.get(var) for var in var_names if var in os.environ}

    entry = {
        "timestamp": time.time(),
        "pid": os.getpid(),
        "args": sys.argv[1:],
        "env": recorded_env,
    }

    path = Path(log_file)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(entry) + "\n")


def log_signal(sig_name: str) -> None:
    sig_log = os.environ.get("FAKE_AGY_SIGNAL_LOG_FILE")
    if sig_log:
        p = Path(sig_log)
        p.parent.mkdir(parents=True, exist_ok=True)
        with open(p, "a", encoding="utf-8") as f:
            f.write(f"{sig_name}:{os.getpid()}\n")


def handle_signals() -> None:
    def handler(signum, frame):
        if hasattr(signal, "Signals"):
            name = signal.Signals(signum).name
        else:
            name = str(signum)
        log_signal(name)
        sys.exit(128 + signum)

    for sig in (signal.SIGTERM, signal.SIGINT):
        try:
            signal.signal(sig, handler)
        except (ValueError, AttributeError):
            pass

    if hasattr(signal, "SIGBREAK"):
        try:
            signal.signal(signal.SIGBREAK, handler)
        except (ValueError, AttributeError):
            pass


def main() -> int:
    handle_signals()
    record_invocation()

    mode = os.environ.get("FAKE_AGY_MODE", "").lower()
    version = os.environ.get("FAKE_AGY_VERSION", "1.2.6")

    # If --version argument is passed, respect it unless overridden
    if "--version" in sys.argv or "-v" in sys.argv:
        print(version)
        return 0

    if mode == "version":
        print(version)
        return 0

    if mode == "auth_error":
        msg = os.environ.get(
            "FAKE_AGY_STDERR",
            "Error: Authentication failed or credentials expired. "
            "Run 'agy auth login'.",
        )
        print(msg, file=sys.stderr)
        return int(os.environ.get("FAKE_AGY_EXIT_CODE", "1"))

    if mode == "quota_error":
        msg = os.environ.get(
            "FAKE_AGY_STDERR",
            "Error: You have exceeded your current quota. Please check your plan.",
        )
        print(msg, file=sys.stderr)
        return int(os.environ.get("FAKE_AGY_EXIT_CODE", "1"))

    if mode == "rate_limit_error":
        msg = os.environ.get(
            "FAKE_AGY_STDERR",
            "Error: 429 Resource exhausted: Rate limit reached.",
        )
        print(msg, file=sys.stderr)
        return int(os.environ.get("FAKE_AGY_EXIT_CODE", "1"))

    if mode == "timeout_error":
        msg = os.environ.get(
            "FAKE_AGY_STDERR",
            "Error: Request timed out after 30.0s.",
        )
        print(msg, file=sys.stderr)
        return int(os.environ.get("FAKE_AGY_EXIT_CODE", "1"))

    if mode == "sleep":
        ready_file = os.environ.get("FAKE_AGY_READY_FILE")
        if ready_file:
            Path(ready_file).write_text(str(os.getpid()))
        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            return 130

    if mode == "spawn_child":
        # Spawn a child process running fake_agy in sleep mode
        child_env = dict(os.environ)
        child_env["FAKE_AGY_MODE"] = "sleep"
        child_proc = subprocess.Popen(
            [sys.executable, "-m", "magy.testing.fake_agy"],
            env=child_env,
        )
        child_pid_file = os.environ.get("FAKE_AGY_CHILD_PID_FILE")
        if child_pid_file:
            Path(child_pid_file).write_text(str(child_proc.pid))

        ready_file = os.environ.get("FAKE_AGY_READY_FILE")
        if ready_file:
            Path(ready_file).write_text(str(os.getpid()))

        try:
            while True:
                time.sleep(0.5)
        except KeyboardInterrupt:
            child_proc.terminate()
            return 130
        finally:
            try:
                child_proc.poll()
            except Exception:
                pass

    if mode == "custom":
        stdout_msg = os.environ.get("FAKE_AGY_STDOUT")
        stderr_msg = os.environ.get("FAKE_AGY_STDERR")
        if stdout_msg:
            print(stdout_msg)
        if stderr_msg:
            print(stderr_msg, file=sys.stderr)
        return int(os.environ.get("FAKE_AGY_EXIT_CODE", "0"))

    # Default success response
    print("Fake Agy: command executed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
