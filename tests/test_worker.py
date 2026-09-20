import os
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil

from magy.profiles import add_profile, get_profile, remove_profile
from magy.runs import (
    cancel_run,
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


def test_worker_timeout_stays_terminal_when_cleanup_fails(fake_agy, monkeypatch):
    import magy.worker

    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "sleep")
    original_terminate = magy.worker._terminate_pid_tree
    calls = 0

    def _fail_once(*args, **kwargs):
        nonlocal calls
        calls += 1
        if calls == 1:
            raise OSError("cleanup failed")
        return original_terminate(*args, **kwargs)

    monkeypatch.setattr(magy.worker, "_terminate_pid_tree", _fail_once)
    add_profile("worker-timeout-cleanup-p", kind="managed")
    req, _, _ = create_run(
        prompt="Timeout cleanup failure",
        profile="worker-timeout-cleanup-p",
        timeout=0.2,
    )

    assert run_worker(req.run_id) == 1
    assert get_run_state(req.run_id).status == "timed_out"


def test_worker_timeout_records_pending_cleanup_until_retry(fake_agy, monkeypatch):
    import magy.worker

    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "sleep")
    original_terminate = magy.worker._terminate_pid_tree
    monkeypatch.setattr(
        magy.worker,
        "_terminate_pid_tree",
        lambda *args, **kwargs: False,
    )
    add_profile("worker-timeout-pending-p", kind="managed")
    req, _, _ = create_run(
        prompt="Timeout pending cleanup",
        profile="worker-timeout-pending-p",
        timeout=0.2,
    )

    assert run_worker(req.run_id) == 1
    state = get_run_state(req.run_id)
    assert state.status == "timed_out"
    assert state.cleanup_pending is True

    monkeypatch.setattr(magy.worker, "_terminate_pid_tree", original_terminate)
    assert get_run_status(req.run_id).cleanup_pending is False


def test_worker_cleans_descendants_before_completed_state(
    fake_agy, monkeypatch, tmp_path: Path
):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "spawn_child_exit")
    child_pid_file = tmp_path / "completed-child.pid"
    monkeypatch.setenv("FAKE_AGY_CHILD_PID_FILE", str(child_pid_file))
    add_profile("worker-complete-tree-p", kind="managed")
    req, _, _ = create_run(
        prompt="Complete after child",
        profile="worker-complete-tree-p",
    )

    assert run_worker(req.run_id) == 0
    assert get_run_state(req.run_id).status == "completed"
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(child_pid)


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
    assert final_state.error == "Worker execution failed"


def test_cancel_waits_for_child_pid_publication(fake_agy, monkeypatch):
    import magy.worker

    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "sleep")
    add_profile("worker-cancel-race-p", kind="managed")
    req, _, _ = create_run(
        prompt="Cancellation publication race",
        profile="worker-cancel-race-p",
    )

    spawned = threading.Event()
    release_spawn = threading.Event()
    original_popen = magy.worker.subprocess.Popen

    def _blocking_popen(*args, **kwargs):
        proc = original_popen(*args, **kwargs)
        command = args[0] if args else kwargs.get("args", [])
        if "--print" in command:
            spawned.set()
            assert release_spawn.wait(timeout=5.0)
        return proc

    monkeypatch.setattr(magy.worker.subprocess, "Popen", _blocking_popen)

    with ThreadPoolExecutor(max_workers=2) as pool:
        worker_future = pool.submit(run_worker, req.run_id)
        assert spawned.wait(timeout=5.0)
        cancel_future = pool.submit(cancel_run, req.run_id)
        time.sleep(0.1)
        assert not cancel_future.done()
        release_spawn.set()
        status = cancel_future.result(timeout=10.0)
        worker_future.result(timeout=10.0)

    final_state = get_run_state(req.run_id)
    assert status.status == "cancelled"
    assert final_state.status == "cancelled"
    assert final_state.child_pid is not None
    assert not psutil.pid_exists(final_state.child_pid)


def test_cancel_kills_fake_agy_descendants(fake_agy, monkeypatch, tmp_path: Path):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "spawn_child")
    ready_file = tmp_path / "ready.txt"
    child_pid_file = tmp_path / "child.pid"
    monkeypatch.setenv("FAKE_AGY_READY_FILE", str(ready_file))
    monkeypatch.setenv("FAKE_AGY_CHILD_PID_FILE", str(child_pid_file))
    add_profile("worker-tree-cancel-p", kind="managed")
    req, _, _ = create_run(
        prompt="Tree cancellation",
        profile="worker-tree-cancel-p",
    )

    with ThreadPoolExecutor(max_workers=1) as pool:
        worker_future = pool.submit(run_worker, req.run_id)
        deadline = time.time() + 5.0
        while time.time() < deadline and not (
            ready_file.exists() and child_pid_file.exists()
        ):
            time.sleep(0.05)
        assert ready_file.exists()
        assert child_pid_file.exists()
        parent_pid = int(ready_file.read_text(encoding="utf-8"))
        child_pid = int(child_pid_file.read_text(encoding="utf-8"))

        status = cancel_run(req.run_id)
        worker_future.result(timeout=10.0)

    assert status.status == "cancelled"
    assert not psutil.pid_exists(parent_pid)
    assert not psutil.pid_exists(child_pid)


def test_worker_rejects_profile_recreated_after_selection(fake_agy, monkeypatch):
    import magy.worker

    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    add_profile("worker-aba-p", kind="managed")
    req, _, _ = create_run(prompt="ABA", profile="worker-aba-p")
    original_select = magy.worker.select_profile

    def _select_then_recreate(*args, **kwargs):
        selected = original_select(*args, **kwargs)
        remove_profile("worker-aba-p")
        add_profile("worker-aba-p", kind="managed")
        return selected

    monkeypatch.setattr(magy.worker, "select_profile", _select_then_recreate)

    assert run_worker(req.run_id) == 1
    assert get_run_state(req.run_id).status == "failed"


def test_worker_rejects_unsupported_auto_approval(fake_agy, monkeypatch):
    import magy.worker

    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setattr(
        magy.worker,
        "get_agy_capabilities",
        lambda executable: {
            "supports_json_output": False,
            "supports_auto_approval": False,
            "supports_add_dir": False,
        },
    )
    add_profile("worker-capability-p", kind="managed")
    req, _, _ = create_run(
        prompt="Capabilities",
        profile="worker-capability-p",
        auto_approval=True,
    )

    assert run_worker(req.run_id) == 1
    assert get_run_state(req.run_id).status == "failed"


def test_invalid_config_does_not_overwrite_cancellation(monkeypatch):
    import magy.worker
    from magy.config import ConfigLoadResult, MagyConfig

    req, _, _ = create_run(prompt="Cancelled invalid config")

    def _cancel_then_load():
        cancel_run(req.run_id)
        return ConfigLoadResult(MagyConfig(), error="invalid config")

    monkeypatch.setattr(magy.worker, "load_config_result", _cancel_then_load)

    assert run_worker(req.run_id) == 1
    assert get_run_state(req.run_id).status == "cancelled"
