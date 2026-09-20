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
    "XDG_CONFIG_HOME",
    "XDG_DATA_HOME",
    "XDG_CACHE_HOME",
    "XDG_STATE_HOME",
]


def record_invocation() -> None:
    if os.environ.get("FAKE_AGY_IS_MULTICALL") == "1":
        side_effect_file = os.environ.get("FAKE_MULTICALL_SIDE_EFFECT_FILE")
        if side_effect_file:
            p = Path(side_effect_file)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(
                f"FORBIDDEN_MULTICALL_EXECUTED:{sys.argv[0]}\n", encoding="utf-8"
            )

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
        "argv0": sys.argv[0],
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


def get_account_marker_path() -> Path | None:
    """Return path to profile-local account alias marker file."""
    home = os.environ.get("HOME") or os.environ.get("USERPROFILE")
    if not home:
        return None
    return Path(home) / ".gemini" / "account_alias.txt"


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

    if "--help" in sys.argv or "-h" in sys.argv:
        caps = os.environ.get("FAKE_AGY_CAPABILITIES", "json,auto_approval,add_dir")
        lines = ["Usage of agy:"]
        if "json" in caps:
            lines.append("  --output-format Output format for print mode")
        if "auto_approval" in caps:
            lines.append("  --dangerously-skip-permissions Auto-approve permissions")
        if "add_dir" in caps:
            lines.append("  --add-dir Add a directory to the workspace")
        print("\n".join(lines))
        return 0

    # Handle interactive probe mode
    if mode == "interactive_probe" or "--interactive-probe" in sys.argv:
        tty_file = os.environ.get("FAKE_AGY_TTY_STATUS_FILE")
        if tty_file:
            tty_info = (
                f"0={sys.stdin.isatty()},"
                f"1={sys.stdout.isatty()},"
                f"2={sys.stderr.isatty()}"
            )
            Path(tty_file).write_text(tty_info, encoding="utf-8")
        sys.stdout.write("AUTH_PROMPT> ")
        sys.stdout.flush()
        line = sys.stdin.readline()
        sys.stdout.write(f"RECEIVED:{line.strip()}\n")
        sys.stdout.flush()
        return 0

    # Write log content if requested
    log_content_to_write = os.environ.get("FAKE_AGY_WRITE_LOG")
    if log_content_to_write:
        log_arg_path: str | None = None
        for i, a in enumerate(sys.argv):
            if a in ("--log-file", "-l", "--log") and i + 1 < len(sys.argv):
                log_arg_path = sys.argv[i + 1]
            elif a.startswith(("--log-file=", "--log=")):
                log_arg_path = a.split("=", 1)[1]
        if log_arg_path:
            lp = Path(log_arg_path)
            lp.parent.mkdir(parents=True, exist_ok=True)
            with open(lp, "a", encoding="utf-8") as f:
                f.write(log_content_to_write + "\n")

    marker_path = get_account_marker_path()

    # Record account alias if requested via argument or environment
    alias_to_record = os.environ.get("FAKE_AGY_RECORD_ALIAS")
    if "--record-alias" in sys.argv:
        idx = sys.argv.index("--record-alias")
        if idx + 1 < len(sys.argv):
            alias_to_record = sys.argv[idx + 1]

    if alias_to_record and marker_path:
        marker_path.parent.mkdir(parents=True, exist_ok=True)
        marker_path.write_text(alias_to_record, encoding="utf-8")

    # Handle sleep request
    if "--sleep" in sys.argv:
        idx = sys.argv.index("--sleep")
        sleep_dur = 0.5
        if idx + 1 < len(sys.argv):
            try:
                sleep_dur = float(sys.argv[idx + 1])
            except ValueError:
                sleep_dur = 0.5
        ready_file = os.environ.get("FAKE_AGY_READY_FILE")
        if ready_file:
            Path(ready_file).write_text(str(os.getpid()))
        time.sleep(sleep_dur)

    # Handle logout simulation
    if "--logout" in sys.argv:
        if marker_path and marker_path.exists():
            marker_path.unlink()
        print("Logged out account alias.")
        return 0

    # Handle whoami query
    if "whoami" in sys.argv or "--whoami" in sys.argv:
        if marker_path and marker_path.exists():
            print(marker_path.read_text(encoding="utf-8").strip())
            return 0
        print("unauthenticated", file=sys.stderr)
        return 1

    if os.environ.get("FAKE_AGY_REQUIRE_AUTH") in ("1", "true", "True"):
        if not (marker_path and marker_path.exists()):
            print("Error: Authentication required. Run login.", file=sys.stderr)
            return 1

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

    if mode == "spawn_child_exit":
        child_env = dict(os.environ)
        child_env["FAKE_AGY_MODE"] = "sleep"
        child_proc = subprocess.Popen(
            [sys.executable, "-m", "magy.testing.fake_agy"],
            env=child_env,
        )
        child_pid_file = os.environ.get("FAKE_AGY_CHILD_PID_FILE")
        if child_pid_file:
            Path(child_pid_file).write_text(str(child_proc.pid))
        print("Fake Agy: parent completed after spawning child.")
        return 0

    if mode == "custom":
        stdout_msg = os.environ.get("FAKE_AGY_STDOUT")
        stderr_msg = os.environ.get("FAKE_AGY_STDERR")
        if stdout_msg:
            print(stdout_msg)
        if stderr_msg:
            print(stderr_msg, file=sys.stderr)
        return int(os.environ.get("FAKE_AGY_EXIT_CODE", "0"))

    # Default success response
    if "--output-format" in sys.argv:
        idx = sys.argv.index("--output-format")
        if idx + 1 < len(sys.argv) and sys.argv[idx + 1] == "json":
            print(json.dumps({"response": "Fake Agy: command executed successfully."}))
            return 0
    for a in sys.argv:
        if a == "--output-format=json":
            print(json.dumps({"response": "Fake Agy: command executed successfully."}))
            return 0

    print("Fake Agy: command executed successfully.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
