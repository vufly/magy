import os
import select
import subprocess
import sys
import time

import pytest

from magy.cli import main
from magy.profiles import add_profile, get_profile, run_in_profile


@pytest.mark.skipif(os.name == "nt", reason="PTY integration tests are POSIX-only")
def test_pty_interactive_auth_prompt_and_response(fake_agy, monkeypatch, tmp_path):
    import pty

    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    tty_status = tmp_path / "tty_status.txt"
    monkeypatch.setenv("FAKE_AGY_TTY_STATUS_FILE", str(tty_status))

    add_profile("auth-tty-p")

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "magy.cli",
                "profile",
                "auth",
                "auth-tty-p",
                "--",
                "--interactive-probe",
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            env=os.environ,
        )
    finally:
        os.close(slave)

    # 1. Assert prompt bytes become visible BEFORE any input is sent
    prompt_seen = False
    received_bytes = b""
    start = time.time()
    while time.time() - start < 5.0:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            received_bytes += chunk
            if b"AUTH_PROMPT>" in received_bytes:
                prompt_seen = True
                break
        if proc.poll() is not None:
            break

    assert prompt_seen, (
        f"Prompt was not emitted before input; received: {received_bytes!r}"
    )

    # 2. Send input response
    os.write(master, b"my-auth-token-probe\n")

    # 3. Read until completion
    while time.time() - start < 5.0:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            received_bytes += chunk
            if b"RECEIVED:my-auth-token-probe" in received_bytes:
                break
        if proc.poll() is not None:
            break

    ret = proc.wait(timeout=5.0)
    os.close(master)

    assert ret == 0
    assert b"RECEIVED:my-auth-token-probe" in received_bytes

    # Assert TTY status on stdin (0), stdout (1), and stderr (2)
    assert tty_status.exists()
    status_text = tty_status.read_text(encoding="utf-8").strip()
    assert "0=True" in status_text
    assert "1=True" in status_text
    assert "2=True" in status_text


@pytest.mark.skipif(os.name == "nt", reason="PTY integration tests are POSIX-only")
def test_pty_passthrough_interactive(fake_agy, monkeypatch, tmp_path):
    import pty

    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    add_profile("pass-tty-p")

    master, slave = pty.openpty()
    try:
        proc = subprocess.Popen(
            [
                sys.executable,
                "-m",
                "magy.cli",
                "--profile",
                "pass-tty-p",
                "--",
                "--interactive-probe",
            ],
            stdin=slave,
            stdout=slave,
            stderr=slave,
            close_fds=True,
            env=os.environ,
        )
    finally:
        os.close(slave)

    prompt_seen = False
    received_bytes = b""
    start = time.time()
    while time.time() - start < 5.0:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            received_bytes += chunk
            if b"AUTH_PROMPT>" in received_bytes:
                prompt_seen = True
                break
        if proc.poll() is not None:
            break

    assert prompt_seen
    os.write(master, b"probe-reply\n")

    while time.time() - start < 5.0:
        r, _, _ = select.select([master], [], [], 0.1)
        if r:
            chunk = os.read(master, 1024)
            if not chunk:
                break
            received_bytes += chunk
            if b"RECEIVED:probe-reply" in received_bytes:
                break
        if proc.poll() is not None:
            break

    ret = proc.wait(timeout=5.0)
    os.close(master)

    assert ret == 0
    assert b"RECEIVED:probe-reply" in received_bytes


def test_redirected_stdout_scriptability(fake_agy, monkeypatch, capfd):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    add_profile("script-p")

    ret = main(["--profile", "script-p", "--", "models"])
    assert ret == 0

    captured = capfd.readouterr()
    # Child stdout must remain clean and exact
    assert "Fake Agy: command executed successfully." in captured.out
    assert "[magy]" not in captured.out

    # Magy diagnostics must be on stderr
    assert "[magy] using profile: script-p" in captured.err


def test_inherited_mode_private_log_injection_and_classification(
    fake_agy, monkeypatch, tmp_path
):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "custom")
    monkeypatch.setenv("FAKE_AGY_EXIT_CODE", "1")
    monkeypatch.setenv(
        "FAKE_AGY_WRITE_LOG", "Error: 429 Resource exhausted: Rate limit reached."
    )

    add_profile("log-class-p")

    # Inherited mode (capture_output=False) with update_health=True
    ret = run_in_profile("log-class-p", ["models"], update_health=True)
    assert ret == 1

    # Injected log file should classify the profile
    prof = get_profile("log-class-p")
    assert prof.health == "rate-limited"
    assert prof.cooldown_until is not None


def test_inherited_mode_relative_caller_log_resolved_against_cwd(
    fake_agy, monkeypatch, tmp_path
):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "custom")
    monkeypatch.setenv("FAKE_AGY_EXIT_CODE", "1")
    monkeypatch.setenv(
        "FAKE_AGY_WRITE_LOG",
        "Error: Please sign in to view available models. "
        "Launch the CLI without arguments to sign in.",
    )

    add_profile("cwd-log-p")
    sub_cwd = tmp_path / "sub_project"
    sub_cwd.mkdir(parents=True, exist_ok=True)

    ret = run_in_profile(
        "cwd-log-p",
        ["models", "--log-file", "local_caller.log"],
        cwd=sub_cwd,
        update_health=True,
    )
    assert ret == 1

    # Log was written relative to sub_cwd
    expected_log = sub_cwd / "local_caller.log"
    assert expected_log.exists()

    prof = get_profile("cwd-log-p")
    assert prof.health == "auth-required"
    assert prof.cooldown_until is not None
