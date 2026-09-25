import os
import shutil
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from magy.agy import get_agy_capabilities, resolve_agy_executable
from magy.config import load_config_result
from magy.routing import select_profile


@dataclass
class HeadfulRun:
    profile: str
    session: str
    pane_id: str
    workspace: str


def start_headful_run(
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
    auto_approval: bool = True,
) -> HeadfulRun:
    """Launch an interactive Magy run in a new pane in the current Zellij session."""
    zellij = shutil.which("zellij")
    if zellij is None:
        raise RuntimeError(
            "Headful mode requires Zellij installed and available on PATH"
        )

    if not os.environ.get("ZELLIJ") or not os.environ.get("ZELLIJ_SESSION_NAME"):
        raise RuntimeError(
            "Headful mode requires Magy MCP to run inside an active Zellij session; "
            "start OpenCode from Zellij and reconnect its MCP server"
        )

    workspace_path = Path(workspace).expanduser() if workspace else Path.cwd()
    try:
        workspace_path = workspace_path.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise ValueError("Workspace directory does not exist") from exc
    if not workspace_path.is_dir():
        raise ValueError("Workspace must be a directory")

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
    if auto_approval and not get_agy_capabilities(executable).get(
        "supports_auto_approval", False
    ):
        raise RuntimeError(
            "Installed Agy does not support headful auto-approval; update Agy or "
            "set auto_approval=False"
        )

    selected = select_profile(explicit_name=profile)
    agy_args = ["--prompt-interactive", prompt]
    if auto_approval:
        agy_args.append("--dangerously-skip-permissions")
    if model:
        agy_args.extend(["--model", model])
    if agent:
        agy_args.extend(["--agent", agent])
    if effort:
        agy_args.extend(["--effort", effort])
    if mode:
        agy_args.extend(["--mode", mode])
    if sandbox:
        agy_args.append("--sandbox")
    for directory in additional_dirs or []:
        agy_args.extend(["--add-dir", directory])

    command = [
        zellij,
        "action",
        "new-pane",
        "--direction",
        "right",
        "--cwd",
        str(workspace_path),
        "--name",
        f"Magy {selected.name}",
        "--",
        sys.executable,
        "-m",
        "magy.cli",
        "--profile",
        selected.name,
        "--",
        *agy_args,
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
        raise RuntimeError("Zellij could not create a headful Magy pane") from exc
    if result.returncode != 0:
        raise RuntimeError(
            "Zellij could not create a headful Magy pane; verify the active session "
            "and available pane space"
        )

    pane_id = result.stdout.strip().splitlines()
    if not pane_id:
        raise RuntimeError("Zellij created no pane for the headful Magy run")

    return HeadfulRun(
        profile=selected.name,
        session=os.environ["ZELLIJ_SESSION_NAME"],
        pane_id=pane_id[-1].strip(),
        workspace=str(workspace_path),
    )
