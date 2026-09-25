import json
import os
import subprocess
import sys
from pathlib import Path

from magy.reviews import get_review_lock
from magy.runs import _process_create_time
from magy.storage import atomic_write_json, read_json


def _render_event(event: dict) -> str | None:
    """Convert a stream-json event to a human-readable line, or None to skip."""
    kind = event.get("event")

    if kind == "init":
        conv_id = event.get("conversation_id", "?")
        short_id = conv_id[:8] if conv_id else "?"
        mode = event.get("init", {}).get("permission_mode", "?")
        return f"▶ Agy started  conversation={short_id}  mode={mode}"

    if kind == "step_update":
        upd = event.get("step_update", {})
        step_type = upd.get("step_type", "")
        state = upd.get("state", "")

        if step_type == "agent_response" and state == "DONE":
            delta = upd.get("text_delta", "")
            usage = upd.get("usage", {})
            tokens = usage.get("output_tokens", 0)
            thinking = usage.get("thinking_tokens", 0)
            duration = upd.get("duration_seconds", 0)
            header = f"[response  {duration:.1f}s  tokens={tokens}"
            if thinking:
                header += f"  thinking={thinking}"
            header += "]"
            if delta:
                return f"{header}\n{delta.rstrip()}"
            return header

        if step_type == "tool":
            tool = upd.get("tool_name", "?")
            tool_info = upd.get("tool_info", {})
            params = tool_info.get("parameters", {})

            if state == "ACTIVE":
                summary = ""
                if params:
                    first_val = next(iter(params.values()), "")
                    if isinstance(first_val, str) and first_val:
                        summary = f": {first_val[:80]}"
                return f"  ⚙ {tool}{summary}"

            if state == "DONE":
                dur = upd.get("duration_seconds", 0)
                return f"  ✓ {tool}  ({dur:.2f}s)"

            if state == "ERROR":
                dur = upd.get("duration_seconds", 0)
                err = tool_info.get("error", {}).get("message", "?")
                short_err = err.split("\n")[0][:120]
                return f"  ✗ {tool}  ({dur:.2f}s)  {short_err}"

    if kind == "result":
        res = event.get("result", {})
        status = res.get("status", "UNKNOWN")
        dur = res.get("duration_seconds", 0)
        usage = res.get("usage", {})
        total = usage.get("total_tokens", 0)
        denied = res.get("denied_actions", [])
        line = f"● {status}  {dur:.1f}s  total_tokens={total}"
        if denied:
            names = ", ".join(d.get("display_name", "?") for d in denied)
            line += f"  denied=[{names}]"
        return line

    return None


def _process_stream(
    proc: subprocess.Popen,
    log_path: Path,
) -> int:
    """
    Read stream-json lines from stdout, render to terminal, write raw to log.

    stderr is inherited (goes directly to Zellij pane for real-time display).
    stdout carries NDJSON events — rendered as human-readable for the pane,
    and logged raw for machine consumption.
    """
    final_status = None

    assert proc.stdout is not None
    with open(log_path, "a", encoding="utf-8") as log_f:
        for raw_line in proc.stdout:
            raw_line = raw_line.rstrip("\n")
            if not raw_line:
                continue

            # Always log raw NDJSON for machine consumption
            log_f.write(raw_line + "\n")
            log_f.flush()

            # Try to parse and render human-readable
            try:
                event = json.loads(raw_line)
            except json.JSONDecodeError:
                # Non-JSON prefix (e.g. magy routing header) — pass through as-is
                print(raw_line, flush=True)
                continue

            rendered = _render_event(event)
            if rendered is not None:
                print(rendered, flush=True)

            if event.get("event") == "result":
                final_status = event.get("result", {}).get("status", "")

    proc.wait()

    if final_status == "SUCCESS":
        return 0
    if final_status is not None:
        return 1
    # No result event seen — fall back to process exit code
    return proc.returncode


def run_review_runner(review_dir_path: str) -> int:
    """Execute Agy with stream-json output, rendering human-readable events in pane.

    Uses --output-format stream-json so events are emitted line-by-line as they
    happen, giving real-time visibility in the Zellij pane without PTY or ANSI
    escape codes in the log file.

    stderr is inherited so agy's own diagnostics/progress appear directly in
    the pane without being captured into the log.
    """
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

    # stream-json gives one NDJSON event per line as they happen — true streaming
    cmd = [
        sys.executable,
        "-m",
        "magy.cli",
        "--profile",
        req["profile"],
        "--",
        "--print",
        req["prompt"],
        "--output-format",
        "stream-json",
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

    ws = req.get("workspace")
    if ws and Path(ws).is_dir():
        os.chdir(ws)

    os.environ["MAGY_REVIEW_ID"] = req["review_id"]
    # Also set MAGY_RUN_ID so _terminate_pid_tree can verify this process
    # during cancel_review_run (it checks MAGY_RUN_ID in process environment).
    os.environ["MAGY_RUN_ID"] = req["review_id"]

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        # stderr inherited: agy's diagnostics and permission notices go directly
        # to the Zellij pane for real-time human visibility, not into the log.
        stderr=None,
        text=True,
        bufsize=1,  # line-buffered for real-time delivery
    )

    return _process_stream(proc, log_path)


def main(argv: list[str] | None = None) -> int:
    """CLI entrypoint for review runner process."""
    args = argv if argv is not None else sys.argv[1:]
    if not args:
        print("Usage: python -m magy.review_runner <review_dir>", file=sys.stderr)
        return 1
    return run_review_runner(args[0])


if __name__ == "__main__":
    sys.exit(main())
