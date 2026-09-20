import json
import os
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import psutil
import pytest

from magy.profiles import add_profile
from magy.runs import (
    _process_create_time,
    _terminate_pid_tree,
    cancel_run,
    claim_run,
    create_run,
    get_run_dir,
    get_run_request,
    get_run_result,
    get_run_state,
    get_run_status,
    publish_worker_process,
    start_run,
    update_run_state,
    wait_run,
)


def test_create_run_persists_request_and_state(tmp_path: Path):
    req, state, is_new = create_run(
        prompt="Tell me a joke",
        workspace=str(tmp_path),
        model="gpt-4",
        timeout=30.0,
    )

    assert is_new is True
    assert req.run_id == state.run_id
    assert state.status == "queued"
    assert state.started_at is None
    assert state.finished_at is None

    run_dir = get_run_dir(req.run_id)
    assert (run_dir / "request.json").exists()
    assert (run_dir / "state.json").exists()
    assert (run_dir / "stdout.log").exists()
    assert (run_dir / "stderr.log").exists()
    assert (run_dir / "agy.log").exists()

    loaded_req = get_run_request(req.run_id)
    assert loaded_req.prompt == "Tell me a joke"
    assert loaded_req.workspace == str(tmp_path)
    assert loaded_req.model == "gpt-4"

    loaded_state = get_run_state(req.run_id)
    assert loaded_state.status == "queued"


def test_idempotency_key_returns_existing_run(tmp_path: Path):
    req1, state1, is_new1 = create_run(
        prompt="Calculate 2+2",
        idempotency_key="math-calc-1",
    )
    assert is_new1 is True

    req2, state2, is_new2 = create_run(
        prompt="Calculate 2+2",
        idempotency_key="math-calc-1",
    )
    assert is_new2 is False
    assert req2.run_id == req1.run_id
    assert state2.run_id == state1.run_id


def test_concurrent_idempotency_key_creates_one_run():
    def _create(_):
        return create_run(
            prompt="Concurrent idempotency",
            idempotency_key="concurrent-key",
        )

    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(_create, range(4)))

    assert len({request.run_id for request, _, _ in results}) == 1
    assert sum(1 for _, _, is_new in results if is_new) == 1


@pytest.mark.parametrize(
    "status", ["running", "completed", "failed", "timed_out", "cancelled"]
)
def test_worker_claim_rejects_nonqueued_status(status: str):
    req, state, _ = create_run(prompt="Claim test")
    state_file = get_run_dir(req.run_id) / "state.json"
    data = json.loads(state_file.read_text(encoding="utf-8"))
    data["status"] = status
    state_file.write_text(json.dumps(data), encoding="utf-8")

    claimed_state, claimed = claim_run(req.run_id, 12345)

    assert claimed is False
    assert claimed_state.status == status


def test_identical_prompts_without_idempotency_key_are_independent():
    req1, _, is_new1 = create_run(prompt="Identical prompt")
    req2, _, is_new2 = create_run(prompt="Identical prompt")

    assert is_new1 is True
    assert is_new2 is True
    assert req1.run_id != req2.run_id


def test_get_run_status_omits_prompts_commands_and_secrets(tmp_path: Path):
    req, state, _ = create_run(
        prompt="Super secret prompt with token sensitive-12345",
        workspace=str(tmp_path),
    )

    status = get_run_status(req.run_id)
    status_dict = status.to_dict()

    # Verify expected safe fields
    assert status_dict["run_id"] == req.run_id
    assert status_dict["status"] == "queued"
    assert "created_at" in status_dict
    assert "retryable" in status_dict

    # Sensitive fields must NOT be present
    assert "prompt" not in status_dict
    assert "argv" not in status_dict
    assert "command" not in status_dict
    assert "stdout_path" not in status_dict
    assert "stderr_path" not in status_dict
    assert "log_path" not in status_dict
    assert "sensitive-12345" not in json.dumps(status_dict)


def test_get_run_result_bounded_chunks_and_offsets():
    req, state, _ = create_run(prompt="Read test")
    stdout_file = Path(state.stdout_path)
    stdout_file.write_text("0123456789ABCDEF", encoding="utf-8")

    # Read chunk 1: 5 bytes
    res1 = get_run_result(req.run_id, offset=0, limit=5)
    assert res1.content == "01234"
    assert res1.offset == 0
    assert res1.next_offset == 5
    assert res1.eof is False

    # Read chunk 2: 5 bytes
    res2 = get_run_result(req.run_id, offset=5, limit=5)
    assert res2.content == "56789"
    assert res2.offset == 5
    assert res2.next_offset == 10
    assert res2.eof is False

    # Read chunk 3: remaining bytes
    res3 = get_run_result(req.run_id, offset=10, limit=10)
    assert res3.content == "ABCDEF"
    assert res3.offset == 10
    assert res3.next_offset == 16


def test_get_run_result_json_detection():
    req, state, _ = create_run(prompt="JSON test")
    stdout_file = Path(state.stdout_path)
    stdout_file.write_text('{"message": "success", "count": 42}', encoding="utf-8")

    res = get_run_result(req.run_id, offset=0, limit=100)
    assert res.is_json is True
    assert "success" in res.content

    stdout_file.write_text("Plain text output", encoding="utf-8")
    res_plain = get_run_result(req.run_id, offset=0, limit=100)
    assert res_plain.is_json is False


def test_get_run_result_validation():
    req, _, _ = create_run(prompt="Validation test")

    with pytest.raises(ValueError, match="Offset cannot be negative"):
        get_run_result(req.run_id, offset=-1)

    with pytest.raises(ValueError, match="Limit must be at least 4 bytes"):
        get_run_result(req.run_id, offset=0, limit=0)

    with pytest.raises(ValueError, match="Limit cannot exceed"):
        get_run_result(req.run_id, offset=0, limit=1024 * 1024 + 1)


def test_get_run_result_preserves_utf8_boundaries():
    req, state, _ = create_run(prompt="UTF-8 test")
    Path(state.stdout_path).write_text("abécd", encoding="utf-8")

    first = get_run_result(req.run_id, offset=0, limit=4)
    second = get_run_result(req.run_id, offset=first.next_offset, limit=4)

    assert first.content + second.content == "abécd"
    assert first.next_offset == 4
    assert second.next_offset == 6

    with pytest.raises(ValueError, match="UTF-8 character boundary"):
        get_run_result(req.run_id, offset=3, limit=4)


def test_spawn_failure_finalizes_run(monkeypatch):
    import magy.runs

    def _fail_spawn(run_id: str):
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(magy.runs, "spawn_detached_worker", _fail_spawn)

    status = start_run("Spawn failure", idempotency_key="spawn-failure-key")

    assert status.status == "failed"
    assert status.error == "Worker process could not be started"


def test_spawn_failure_does_not_overwrite_cancellation(monkeypatch):
    import magy.runs

    def _cancel_then_fail(run_id: str):
        cancel_run(run_id)
        raise OSError("simulated spawn failure")

    monkeypatch.setattr(magy.runs, "spawn_detached_worker", _cancel_then_fail)

    status = start_run("Cancelled spawn")

    assert status.status == "cancelled"


def test_dead_published_worker_is_reconciled_to_failed():
    req, _, _ = create_run(prompt="Dead worker")
    proc = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        env={**os.environ, "MAGY_RUN_ID": req.run_id},
    )
    publish_worker_process(req.run_id, proc.pid)
    try:
        deadline = time.time() + 5.0
        while time.time() < deadline:
            try:
                if psutil.Process(proc.pid).status() == psutil.STATUS_ZOMBIE:
                    break
            except psutil.NoSuchProcess:
                break
            time.sleep(0.05)

        status = get_run_status(req.run_id)
    finally:
        proc.wait(timeout=5.0)

    assert status.status == "failed"
    assert status.error == "Worker process exited before finalizing the run"


def test_dead_worker_reconciliation_sweeps_unpublished_child():
    req, _, _ = create_run(prompt="Unpublished child")
    worker = subprocess.Popen(
        [sys.executable, "-c", "pass"],
        env={**os.environ, "MAGY_RUN_ID": req.run_id},
    )
    publish_worker_process(req.run_id, worker.pid)
    update_run_state(req.run_id, {"status": "running"})
    worker.wait(timeout=5.0)
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "MAGY_RUN_ID": req.run_id},
    )

    status = get_run_status(req.run_id)

    assert status.status == "failed"
    child.wait(timeout=5.0)
    assert child.poll() is not None


def test_stale_queued_run_without_worker_is_reconciled(monkeypatch):
    import magy.runs

    req, _, _ = create_run(prompt="Unpublished worker")
    monkeypatch.setattr(
        magy.runs,
        "WORKER_STARTUP_GRACE_SECONDS",
        0.0,
    )

    status = get_run_status(req.run_id)

    assert status.status == "failed"
    assert status.error == "Worker process exited before finalizing the run"


def test_stale_queued_live_worker_is_terminated(monkeypatch):
    import magy.runs

    req, _, _ = create_run(prompt="Wedged worker")
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "MAGY_RUN_ID": req.run_id},
    )
    publish_worker_process(req.run_id, worker.pid)
    monkeypatch.setattr(magy.runs, "WORKER_STARTUP_GRACE_SECONDS", 0.0)

    status = get_run_status(req.run_id)

    assert status.status == "failed"
    worker.wait(timeout=5.0)


def test_worker_publication_failure_terminates_spawned_worker(monkeypatch):
    import magy.runs

    spawned: list[subprocess.Popen] = []

    def _spawn(run_id: str):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, "MAGY_RUN_ID": run_id},
        )
        spawned.append(proc)
        return proc

    monkeypatch.setattr(magy.runs, "spawn_detached_worker", _spawn)
    monkeypatch.setattr(
        magy.runs,
        "publish_worker_process",
        lambda run_id, pid: (_ for _ in ()).throw(OSError("publish failed")),
    )

    status = start_run("Publication failure")

    assert status.status == "failed"
    assert spawned
    spawned[0].wait(timeout=5.0)
    assert spawned[0].poll() is not None


def test_worker_publication_cleanup_failure_is_retryable(monkeypatch):
    import magy.runs

    spawned: list[subprocess.Popen] = []
    original_terminate = magy.runs._terminate_pid_tree

    def _spawn(run_id: str):
        proc = subprocess.Popen(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            env={**os.environ, "MAGY_RUN_ID": run_id},
        )
        spawned.append(proc)
        return proc

    monkeypatch.setattr(magy.runs, "spawn_detached_worker", _spawn)
    monkeypatch.setattr(
        magy.runs,
        "publish_worker_process",
        lambda run_id, pid: (_ for _ in ()).throw(OSError("publish failed")),
    )
    monkeypatch.setattr(magy.runs, "_terminate_pid_tree", lambda *args, **kwargs: False)

    status = start_run("Publication cleanup failure")

    assert status.status == "failed"
    assert status.cleanup_pending is True
    assert spawned[0].poll() is None

    monkeypatch.setattr(magy.runs, "_terminate_pid_tree", original_terminate)
    recovered = get_run_status(status.run_id)
    assert recovered.cleanup_pending is False
    spawned[0].wait(timeout=5.0)


def test_legacy_raw_error_is_sanitized_from_status(tmp_path: Path):
    req, _, _ = create_run(prompt="Legacy error")
    update_run_state(
        req.run_id,
        {
            "status": "failed",
            "error": f"resolver failed at {tmp_path}/secret/config.json",
        },
    )

    status = get_run_status(req.run_id)

    assert status.error == "Run failed"
    assert str(tmp_path) not in status.error


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux proc marker")
def test_terminate_run_marker_descendant_after_leader_exit(tmp_path: Path):
    run_id = "run_marker_cleanup"
    daemon_pid_file = tmp_path / "daemon.pid"
    daemon_code = (
        "import os, pathlib, signal, time\n"
        "if os.fork() > 0: os._exit(0)\n"
        "os.setsid()\n"
        "if os.fork() > 0: os._exit(0)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path(r'{daemon_pid_file}').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n"
    )
    leader = subprocess.Popen(
        [sys.executable, "-c", daemon_code],
        env={**os.environ, "MAGY_RUN_ID": run_id},
    )
    leader_create_time = _process_create_time(leader.pid)
    leader.wait(timeout=5.0)
    deadline = time.time() + 5.0
    while time.time() < deadline and not daemon_pid_file.exists():
        time.sleep(0.05)
    assert daemon_pid_file.exists()
    daemon_pid = int(daemon_pid_file.read_text(encoding="utf-8"))

    assert _terminate_pid_tree(leader.pid, leader_create_time, run_id)

    deadline = time.time() + 5.0
    while time.time() < deadline and psutil.pid_exists(daemon_pid):
        try:
            if psutil.Process(daemon_pid).status() == psutil.STATUS_ZOMBIE:
                break
        except psutil.NoSuchProcess:
            break
        time.sleep(0.05)
    assert not psutil.pid_exists(daemon_pid) or (
        psutil.Process(daemon_pid).status() == psutil.STATUS_ZOMBIE
    )


def test_terminate_rejects_unmarked_reused_pid():
    proc = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        create_time = _process_create_time(proc.pid)
        assert not _terminate_pid_tree(proc.pid, create_time, "different-run")
        assert proc.poll() is None
    finally:
        proc.kill()
        proc.wait(timeout=5.0)


@pytest.mark.skipif(not sys.platform.startswith("linux"), reason="Linux proc marker")
def test_terminate_rescans_descendant_forked_during_sigterm(tmp_path: Path):
    run_id = "run_signal_fork"
    child_pid_file = tmp_path / "signal-child.pid"
    ready_file = tmp_path / "signal-root.ready"
    root_code = (
        "import os, pathlib, signal, time\n"
        "def handle(signum, frame):\n"
        "    child = os.fork()\n"
        "    if child == 0:\n"
        "        os.setsid()\n"
        "        signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"        pathlib.Path(r'{child_pid_file}').write_text(str(os.getpid()))\n"
        "        time.sleep(30)\n"
        "    os._exit(0)\n"
        "signal.signal(signal.SIGTERM, handle)\n"
        f"pathlib.Path(r'{ready_file}').write_text('ready')\n"
        "time.sleep(30)\n"
    )
    root = subprocess.Popen(
        [sys.executable, "-c", root_code],
        env={**os.environ, "MAGY_RUN_ID": run_id},
    )
    create_time = _process_create_time(root.pid)
    deadline = time.time() + 5.0
    while time.time() < deadline and not ready_file.exists():
        time.sleep(0.05)
    assert ready_file.exists()

    assert _terminate_pid_tree(root.pid, create_time, run_id)
    root.wait(timeout=5.0)
    assert child_pid_file.exists()
    child_pid = int(child_pid_file.read_text(encoding="utf-8"))
    assert not psutil.pid_exists(child_pid) or (
        psutil.Process(child_pid).status() == psutil.STATUS_ZOMBIE
    )


def test_cancel_run_marks_terminal_state():
    req, _, _ = create_run(prompt="Cancel test")
    status = cancel_run(req.run_id)

    assert status.status == "cancelled"
    assert status.finished_at is not None
    assert status.error == "Run was cancelled by user"

    # Subsequent cancellation returns same terminal status
    status2 = cancel_run(req.run_id)
    assert status2.status == "cancelled"


def test_cancel_remains_terminal_when_cleanup_fails(monkeypatch):
    import magy.runs

    req, _, _ = create_run(prompt="Cancel cleanup failure")
    update_run_state(
        req.run_id,
        {
            "status": "running",
            "child_pid": 12345,
            "child_create_time": 1.0,
        },
    )

    def _fail_cleanup(*args, **kwargs):
        raise OSError("simulated cleanup failure")

    monkeypatch.setattr(magy.runs, "_terminate_pid_tree", _fail_cleanup)

    status = cancel_run(req.run_id)

    assert status.status == "cancelled"
    assert status.error is not None
    assert "could not be verified" in status.error

    status_again = cancel_run(req.run_id)
    assert status_again.status == "cancelled"


def test_cancel_terminates_queued_worker_before_child_launch():
    req, _, _ = create_run(prompt="Queued worker cancellation")
    worker = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "MAGY_RUN_ID": req.run_id},
    )
    publish_worker_process(req.run_id, worker.pid)

    status = cancel_run(req.run_id)

    assert status.status == "cancelled"
    worker.wait(timeout=5.0)
    assert worker.poll() is not None


def test_status_recovers_stranded_cancelling_run():
    req, _, _ = create_run(prompt="Stranded cancellation")
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "MAGY_RUN_ID": req.run_id},
    )
    update_run_state(
        req.run_id,
        {
            "status": "cancelling",
            "child_pid": child.pid,
            "child_create_time": _process_create_time(child.pid),
        },
    )

    status = get_run_status(req.run_id)

    assert status.status == "cancelled"
    child.wait(timeout=5.0)


def test_reused_child_pid_does_not_block_marker_cleanup():
    req, _, _ = create_run(prompt="Reused child PID")
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
    )
    marker_child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        env={**os.environ, "MAGY_RUN_ID": req.run_id},
    )
    update_run_state(
        req.run_id,
        {
            "status": "failed",
            "cleanup_pending": True,
            "child_pid": unrelated.pid,
            "child_create_time": 1.0,
        },
    )
    try:
        status = get_run_status(req.run_id)
        assert status.cleanup_pending is False
        assert unrelated.poll() is None
        marker_child.wait(timeout=5.0)
    finally:
        unrelated.kill()
        unrelated.wait(timeout=5.0)


def test_get_run_result_missing_output_is_error():
    req, state, _ = create_run(prompt="Missing output")
    Path(state.stdout_path).unlink()

    with pytest.raises(FileNotFoundError, match="Run output is unavailable"):
        get_run_result(req.run_id)


def test_wait_run_short_poll_returns_completed():
    req, state, _ = create_run(prompt="Wait test")
    # Simulate completion by marking state directly
    state_file = get_run_dir(req.run_id) / "state.json"
    state_data = json.loads(state_file.read_text(encoding="utf-8"))
    state_data["status"] = "completed"
    state_data["exit_code"] = 0
    state_file.write_text(json.dumps(state_data), encoding="utf-8")

    status = wait_run(req.run_id, timeout=2.0)
    assert status.status == "completed"
    assert status.exit_code == 0


def test_retryable_suggests_alternate_available_profile():
    add_profile("failed-p", kind="managed")
    add_profile("alternate-p", kind="managed")

    req, state, _ = create_run(prompt="Retry suggestion test")
    state_file = get_run_dir(req.run_id) / "state.json"
    state_data = json.loads(state_file.read_text(encoding="utf-8"))
    state_data["profile"] = "failed-p"
    state_data["status"] = "failed"
    state_data["health_classification"] = "quota-exhausted"
    state_file.write_text(json.dumps(state_data), encoding="utf-8")

    status = get_run_status(req.run_id)
    assert status.retryable is True
    assert status.suggested_profile == "alternate-p"
