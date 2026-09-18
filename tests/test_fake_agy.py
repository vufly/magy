import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


def test_fake_agy_records_arguments_and_env(fake_agy, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/home")
    monkeypatch.setenv("AGY_CLI_DISABLE_AUTO_UPDATE", "true")
    monkeypatch.setenv("MAGY_PROFILE", "test-profile")

    res = subprocess.run(
        [str(fake_agy.executable), "models", "--verbose"],
        capture_output=True,
        text=True,
        check=True,
    )

    assert res.returncode == 0
    invocations = fake_agy.get_invocations()
    assert len(invocations) == 1
    rec = invocations[0]
    assert rec["args"] == ["models", "--verbose"]
    assert rec["env"]["HOME"] == "/fake/home"
    assert rec["env"]["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert rec["env"]["MAGY_PROFILE"] == "test-profile"


def test_fake_agy_simulate_success(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "success")
    res = subprocess.run(
        [str(fake_agy.executable)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 0
    assert "successfully" in res.stdout


@pytest.mark.parametrize(
    "mode,expected_text,expected_code",
    [
        ("auth_error", "Authentication failed", 1),
        ("quota_error", "exceeded your current quota", 1),
        ("rate_limit_error", "429 Resource exhausted", 1),
        ("timeout_error", "Request timed out", 1),
    ],
)
def test_fake_agy_simulated_errors(
    fake_agy, monkeypatch, mode, expected_text, expected_code
):
    monkeypatch.setenv("FAKE_AGY_MODE", mode)
    res = subprocess.run(
        [str(fake_agy.executable)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == expected_code
    assert expected_text in res.stderr


def test_fake_agy_custom_output(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_MODE", "custom")
    monkeypatch.setenv("FAKE_AGY_STDOUT", "custom-output-stdout")
    monkeypatch.setenv("FAKE_AGY_STDERR", "custom-error-stderr")
    monkeypatch.setenv("FAKE_AGY_EXIT_CODE", "42")

    res = subprocess.run(
        [str(fake_agy.executable)],
        capture_output=True,
        text=True,
    )
    assert res.returncode == 42
    assert "custom-output-stdout" in res.stdout
    assert "custom-error-stderr" in res.stderr


def test_fake_agy_cancellation_sleep(fake_agy, monkeypatch, tmp_path: Path):
    ready_file = tmp_path / "ready.txt"
    monkeypatch.setenv("FAKE_AGY_MODE", "sleep")
    monkeypatch.setenv("FAKE_AGY_READY_FILE", str(ready_file))

    proc = subprocess.Popen(
        [str(fake_agy.executable)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait until process is running
    for _ in range(50):
        if ready_file.exists():
            break
        time.sleep(0.05)
    assert ready_file.exists()

    # Cancel process with SIGTERM
    proc.terminate()
    ret = proc.wait(timeout=5.0)
    assert ret != 0


def test_fake_agy_spawn_child_process(fake_agy, monkeypatch, tmp_path: Path):
    """Test fake Agy child process creation and PID tracking
    for future M3 cancellation tests.
    """
    ready_file = tmp_path / "ready_tree.txt"
    child_pid_file = tmp_path / "child_pid.txt"

    monkeypatch.setenv("FAKE_AGY_MODE", "spawn_child")
    monkeypatch.setenv("FAKE_AGY_READY_FILE", str(ready_file))
    monkeypatch.setenv("FAKE_AGY_CHILD_PID_FILE", str(child_pid_file))

    proc = subprocess.Popen(
        [str(fake_agy.executable)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    )

    # Wait until parent and child are running
    for _ in range(50):
        if ready_file.exists() and child_pid_file.exists():
            break
        time.sleep(0.05)

    assert ready_file.exists()
    assert child_pid_file.exists()

    child_pid = int(child_pid_file.read_text().strip())
    assert child_pid > 0

    # Terminate parent
    proc.terminate()
    proc.wait(timeout=5.0)

    # Clean up child if still running
    try:
        os.kill(child_pid, signal.SIGTERM)
    except OSError:
        pass  # already terminated
