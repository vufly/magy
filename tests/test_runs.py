import json
from pathlib import Path

import pytest

from magy.profiles import add_profile
from magy.runs import (
    cancel_run,
    create_run,
    get_run_dir,
    get_run_request,
    get_run_result,
    get_run_state,
    get_run_status,
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

    with pytest.raises(ValueError, match="Limit must be greater than zero"):
        get_run_result(req.run_id, offset=0, limit=0)


def test_cancel_run_marks_terminal_state():
    req, _, _ = create_run(prompt="Cancel test")
    status = cancel_run(req.run_id)

    assert status.status == "cancelled"
    assert status.finished_at is not None
    assert status.error == "Run was cancelled by user"

    # Subsequent cancellation returns same terminal status
    status2 = cancel_run(req.run_id)
    assert status2.status == "cancelled"


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
