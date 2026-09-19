import os
from pathlib import Path

from magy.profiles import add_profile, get_profile
from magy.runs import (
    create_run,
    get_run_result,
    get_run_state,
    get_run_status,
)
from magy.worker import run_worker


def test_worker_successful_run(fake_agy, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    add_profile("worker-success-p", kind="managed")

    req, state, _ = create_run(
        prompt="Hello worker",
        workspace=str(tmp_path),
        profile="worker-success-p",
    )

    exit_code = run_worker(req.run_id)
    assert exit_code == 0

    final_state = get_run_state(req.run_id)
    assert final_state.status == "completed"
    assert final_state.exit_code == 0
    assert final_state.started_at is not None
    assert final_state.finished_at is not None
    assert final_state.profile == "worker-success-p"

    prof = get_profile("worker-success-p")
    assert prof.health == "healthy"
    assert prof.last_success_at is not None

    result = get_run_result(req.run_id)
    assert "Fake Agy: command executed successfully." in result.content
    assert result.eof is True


def test_worker_auto_round_robin_selection(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    add_profile("rr-w1", kind="managed")
    add_profile("rr-w2", kind="managed")

    req, _, _ = create_run(prompt="Round robin test")
    assert req.profile is None

    exit_code = run_worker(req.run_id)
    assert exit_code == 0

    final_state = get_run_state(req.run_id)
    assert final_state.status == "completed"
    assert final_state.profile in ("rr-w1", "rr-w2")


def test_worker_failure_classification_and_profile_health(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "quota_error")
    monkeypatch.setenv("FAKE_AGY_EXIT_CODE", "1")

    add_profile("worker-quota-p", kind="managed")

    req, _, _ = create_run(
        prompt="Quota test",
        profile="worker-quota-p",
    )

    exit_code = run_worker(req.run_id)
    assert exit_code == 1

    final_state = get_run_state(req.run_id)
    assert final_state.status == "failed"
    assert final_state.exit_code == 1
    assert final_state.health_classification == "quota-exhausted"

    prof = get_profile("worker-quota-p")
    assert prof.health == "quota-exhausted"
    assert prof.cooldown_until is not None

    status = get_run_status(req.run_id)
    assert status.retryable is True


def test_worker_timeout_terminates_and_marks_state(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "sleep")

    add_profile("worker-timeout-p", kind="managed")

    req, _, _ = create_run(
        prompt="Sleep test",
        profile="worker-timeout-p",
        timeout=0.3,
    )

    exit_code = run_worker(req.run_id)
    assert exit_code == 1

    final_state = get_run_state(req.run_id)
    assert final_state.status == "timed_out"
    assert "timed out" in final_state.error.lower()

    prof = get_profile("worker-timeout-p")
    assert prof.health == "timeout"


def test_worker_missing_executable_finalizes_state_as_failed():
    add_profile("worker-missing-exe-p", kind="managed")

    req, _, _ = create_run(
        prompt="Missing exe test",
        profile="worker-missing-exe-p",
    )

    # Force invalid executable
    os.environ["MAGY_AGY_CMD"] = "/nonexistent/invalid/agy"
    try:
        exit_code = run_worker(req.run_id)
        assert exit_code == 1
    finally:
        os.environ.pop("MAGY_AGY_CMD", None)

    final_state = get_run_state(req.run_id)
    assert final_state.status == "failed"
    assert "not found" in final_state.error.lower()
