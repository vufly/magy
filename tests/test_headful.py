import subprocess
from types import SimpleNamespace

import pytest

import magy.headful as headful


@pytest.fixture
def configured_headful(monkeypatch):
    config = SimpleNamespace(agy_cmd=None, agy_resolver=None)
    monkeypatch.setattr(
        headful,
        "load_config_result",
        lambda: SimpleNamespace(error=None, config=config),
    )
    monkeypatch.setattr(
        headful,
        "resolve_agy_executable",
        lambda **kwargs: ("/usr/bin/agy", None),
    )
    monkeypatch.setattr(
        headful,
        "get_agy_capabilities",
        lambda executable: {"supports_auto_approval": True},
    )


def test_start_headful_run_builds_zellij_command_and_defaults_auto_approval(
    tmp_path, monkeypatch, configured_headful
):
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "test-session")
    monkeypatch.setattr(headful.shutil, "which", lambda name: "/usr/bin/zellij")
    monkeypatch.setattr(
        headful,
        "select_profile",
        lambda explicit_name: SimpleNamespace(name=explicit_name or "selected"),
    )
    calls = []

    def fake_run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 0, "terminal_42\n", "")

    monkeypatch.setattr(headful.subprocess, "run", fake_run)

    result = headful.start_headful_run(
        "Inspect source",
        workspace=str(tmp_path),
        profile="review",
        model="gemini-test",
        agent="reviewer",
        effort="high",
        mode="plan",
        sandbox=True,
        additional_dirs=["/tmp/extra"],
    )

    command, kwargs = calls[0]
    assert result.profile == "review"
    assert result.session == "test-session"
    assert result.pane_id == "terminal_42"
    assert result.workspace == str(tmp_path)
    assert kwargs["timeout"] == 15
    assert command[:7] == [
        "/usr/bin/zellij",
        "action",
        "new-pane",
        "--direction",
        "right",
        "--cwd",
        str(tmp_path),
    ]
    assert "--dangerously-skip-permissions" in command
    assert command[command.index("--") + 1 : command.index("--") + 4] == [
        headful.sys.executable,
        "-m",
        "magy.cli",
    ]
    assert "Inspect source" in command
    assert "--model" in command and "gemini-test" in command
    assert "--agent" in command and "reviewer" in command
    assert "--effort" in command and "high" in command
    assert "--mode" in command and "plan" in command
    assert "--sandbox" in command
    assert "--add-dir" in command and "/tmp/extra" in command


def test_start_headful_run_can_leave_approvals_interactive(
    tmp_path, monkeypatch, configured_headful
):
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "test-session")
    monkeypatch.setattr(headful.shutil, "which", lambda name: "/usr/bin/zellij")
    monkeypatch.setattr(
        headful,
        "select_profile",
        lambda explicit_name: SimpleNamespace(name=explicit_name or "selected"),
    )
    command = []
    monkeypatch.setattr(
        headful.subprocess,
        "run",
        lambda args, **kwargs: (
            command.extend(args)
            or subprocess.CompletedProcess(args, 0, "terminal_7\n", "")
        ),
    )

    headful.start_headful_run("Inspect", workspace=str(tmp_path), auto_approval=False)

    assert "--dangerously-skip-permissions" not in command


def test_start_headful_run_requires_active_zellij_session(monkeypatch):
    monkeypatch.setattr(headful.shutil, "which", lambda name: "/usr/bin/zellij")
    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)

    with pytest.raises(RuntimeError, match="active Zellij session"):
        headful.start_headful_run("Inspect")


def test_start_headful_run_requires_zellij_on_path(monkeypatch):
    monkeypatch.setattr(headful.shutil, "which", lambda name: None)

    with pytest.raises(RuntimeError, match="requires Zellij"):
        headful.start_headful_run("Inspect")


def test_start_headful_run_sanitizes_zellij_launch_failure(
    tmp_path, monkeypatch, configured_headful
):
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "test-session")
    monkeypatch.setattr(headful.shutil, "which", lambda name: "/usr/bin/zellij")
    monkeypatch.setattr(
        headful,
        "select_profile",
        lambda explicit_name: SimpleNamespace(name=explicit_name or "selected"),
    )
    monkeypatch.setattr(
        headful.subprocess,
        "run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command, 1, "", "/private/path/with/internal/error"
        ),
    )

    with pytest.raises(RuntimeError, match="verify the active session") as exc_info:
        headful.start_headful_run("Inspect", workspace=str(tmp_path))
    assert "/private/path" not in str(exc_info.value)
