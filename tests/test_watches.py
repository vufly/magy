import json
import os
import shlex
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import magy.watches as watches
from magy.watches import (
    WatchRunStart,
    WatchRunState,
    acquire_repo_lease,
    cancel_watch_run,
    compute_git_diff,
    finalize_watch,
    get_watch_dir,
    get_watch_log,
    get_watch_result,
    get_watch_status,
    release_repo_lease,
    start_watch_run,
    take_git_snapshot,
    validate_git_repository,
    wait_watch_run,
)


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:
    """Create a temporary initialized git repository with a HEAD commit."""
    repo = tmp_path / "test_repo"
    repo.mkdir()
    subprocess.run(["git", "init", str(repo)], check=True, capture_output=True)
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.name", "Test User"], check=True
    )
    subprocess.run(
        ["git", "-C", str(repo), "config", "user.email", "test@example.com"],
        check=True,
    )

    readme = repo / "README.md"
    readme.write_text("# Test Repo\nInitial content\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(repo), "add", "README.md"], check=True)
    subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", "Initial commit"], check=True
    )
    return repo


@pytest.fixture
def mock_environment(monkeypatch, git_repo: Path):
    """Set up mock Zellij and Agy environment."""
    monkeypatch.setenv("ZELLIJ", "0")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "test-session")
    monkeypatch.setattr(watches.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    config = SimpleNamespace(agy_cmd=None, agy_resolver=None)
    monkeypatch.setattr(
        watches,
        "load_config_result",
        lambda: SimpleNamespace(error=None, config=config),
    )
    monkeypatch.setattr(
        watches,
        "resolve_agy_executable",
        lambda **kwargs: ("/usr/bin/agy", None),
    )
    monkeypatch.setattr(
        watches,
        "get_agy_capabilities",
        lambda executable: {
            "supports_auto_approval": True,
            "supports_add_dir": True,
        },
    )
    monkeypatch.setattr(
        watches,
        "select_profile",
        lambda explicit_name: SimpleNamespace(name=explicit_name or "default-p"),
    )
    monkeypatch.setattr(
        watches,
        "spawn_detached_monitor",
        lambda watch_id: SimpleNamespace(pid=9999),
    )

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if any("zellij" in str(arg) for arg in cmd):
            return subprocess.CompletedProcess(cmd, 0, "terminal_42\n", "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fake_run)


def test_validate_git_repository_success(git_repo: Path):
    repo_root, head_commit = validate_git_repository(git_repo)
    assert repo_root == git_repo.resolve()
    assert len(head_commit) == 40


def test_validate_git_repository_non_git(tmp_path: Path):
    non_git = tmp_path / "not_git"
    non_git.mkdir()
    with pytest.raises(ValueError, match="not inside a git repository"):
        validate_git_repository(non_git)


def test_validate_git_repository_no_head(tmp_path: Path):
    empty_repo = tmp_path / "empty_repo"
    empty_repo.mkdir()
    subprocess.run(["git", "init", str(empty_repo)], check=True, capture_output=True)
    with pytest.raises(ValueError, match="HEAD commit"):
        validate_git_repository(empty_repo)


def test_git_snapshot_and_diff_excludes_preexisting_dirty_and_untracked(
    git_repo: Path, tmp_path: Path
):
    # 1. Create pre-existing modifications:
    # Tracked file modified (unstaged)
    readme = git_repo / "README.md"
    readme.write_text("# Test Repo\nPre-existing dirty edit\n", encoding="utf-8")

    # Staged file
    staged = git_repo / "staged.txt"
    staged.write_text("pre-existing staged file\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "staged.txt"], check=True)

    # Untracked non-ignored file
    untracked = git_repo / "untracked.txt"
    untracked.write_text("pre-existing untracked\n", encoding="utf-8")

    # Ignored file
    gitignore = git_repo / ".gitignore"
    gitignore.write_text("ignored.txt\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", ".gitignore"], check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "add gitignore"], check=True
    )

    ignored = git_repo / "ignored.txt"
    ignored.write_text("should be ignored\n", encoding="utf-8")

    # 2. Take baseline snapshot
    baseline_index = tmp_path / "base_idx"
    baseline_tree = take_git_snapshot(git_repo, baseline_index)
    assert len(baseline_tree) == 40

    # 3. Simulate Agy run edits + user manual commit during watch
    agy_new = git_repo / "agy_created.txt"
    agy_new.write_text("created by agy\n", encoding="utf-8")

    readme.write_text(
        "# Test Repo\nPre-existing dirty edit\n+ Agy new line\n", encoding="utf-8"
    )

    manual = git_repo / "manual.txt"
    manual.write_text("user manual commit during watch\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "manual.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "user commit during watch"],
        check=True,
    )

    # 4. Take final snapshot
    final_index = tmp_path / "final_idx"
    final_tree = take_git_snapshot(git_repo, final_index)
    assert len(final_tree) == 40

    # 5. Compute diff
    patch = compute_git_diff(git_repo, baseline_tree, final_tree)

    # Assertions:
    assert "diff --git a/agy_created.txt b/agy_created.txt" in patch
    assert "+created by agy" in patch

    assert "diff --git a/manual.txt b/manual.txt" in patch
    assert "+user manual commit during watch" in patch

    assert "diff --git a/README.md b/README.md" in patch
    assert "+ Agy new line" in patch
    assert "-Initial content" not in patch

    assert "untracked.txt" not in patch
    assert "ignored.txt" not in patch


def test_git_snapshot_empty_diff(git_repo: Path, tmp_path: Path):
    idx1 = tmp_path / "idx1"
    tree1 = take_git_snapshot(git_repo, idx1)

    idx2 = tmp_path / "idx2"
    tree2 = take_git_snapshot(git_repo, idx2)

    diff = compute_git_diff(git_repo, tree1, tree2)
    assert diff == ""


def test_repo_lease_concurrency(git_repo: Path, tmp_path: Path):
    watch1 = "watch_1111111111111111"
    watch2 = "watch_2222222222222222"

    dir1 = get_watch_dir(watch1)
    dir1.mkdir(parents=True, exist_ok=True)
    dir2 = get_watch_dir(watch2)
    dir2.mkdir(parents=True, exist_ok=True)

    st1 = WatchRunState(
        watch_id=watch1,
        status="running",
        repo_root=str(git_repo),
        workspace=str(git_repo),
    )
    (dir1 / "state.json").write_text(json.dumps(st1.to_dict()), encoding="utf-8")

    acquire_repo_lease(git_repo, watch1)

    # Second active run on same repo rejected
    with pytest.raises(
        ValueError, match="Another watched run is already active for this repository"
    ):
        acquire_repo_lease(git_repo, watch2)

    # Different repo allowed concurrently
    other_repo = tmp_path / "other_repo"
    other_repo.mkdir()
    acquire_repo_lease(other_repo, watch2)
    release_repo_lease(other_repo, watch2)

    # Releasing watch1 lease allows watch2
    release_repo_lease(git_repo, watch1)
    acquire_repo_lease(git_repo, watch2)
    release_repo_lease(git_repo, watch2)


def test_start_watch_run_builds_command_and_runner_script(
    mock_environment, git_repo: Path, monkeypatch
):
    calls = []
    real_run = subprocess.run

    def fake_subprocess_run(command, **kwargs):
        if any("zellij" in str(arg) for arg in command):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "terminal_55\n", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fake_subprocess_run)

    monkeypatch.setenv("ZELLIJ_PANE_ID", "42")

    start_res = start_watch_run(
        "Refactor parser",
        workspace=str(git_repo),
        profile="dev-profile",
        model="gemini-3.8-flash",
        agent="code-reviewer",
        effort="high",
        mode="architect",
        sandbox=True,
        additional_dirs=["/tmp/extra"],
        auto_approval=True,
    )

    assert isinstance(start_res, WatchRunStart)
    assert start_res.pane_id == "terminal_55"
    assert start_res.profile == "dev-profile"
    assert start_res.workspace == str(git_repo.resolve())

    # Verify Zellij launch command and inherited origin environment
    zellij_cmd, zellij_kwargs = calls[-1]
    assert zellij_cmd[:9] == [
        "/usr/bin/zellij",
        "run",
        "--floating",
        "--near-current-pane",
        "--no-focus",
        "--name",
        f"magy-task-{start_res.watch_id}",
        "--cwd",
        str(git_repo.resolve()),
    ]
    assert zellij_cmd[9] == "--"
    assert zellij_cmd[10:14] == [
        "bash",
        "-c",
        'exec bash "$1"',
        "magy-watch",
    ]
    assert zellij_kwargs.get("env") is None
    assert os.environ["ZELLIJ_PANE_ID"] == "42"

    # Verify runner script was written with mode 0o700 and no interpolated prompt
    watch_dir = get_watch_dir(start_res.watch_id)
    runner_sh = watch_dir / "runner.sh"
    assert runner_sh.exists()
    assert (runner_sh.stat().st_mode & 0o777) == 0o700
    script_text = runner_sh.read_text(encoding="utf-8")
    assert "Refactor parser" not in script_text
    assert "magy.watch_runner" in script_text
    assert ".exit" in script_text

    req = watches.get_watch_request(start_res.watch_id)
    assert req.prompt == "Refactor parser"
    assert req.model == "gemini-3.8-flash"
    assert req.agent == "code-reviewer"
    assert req.auto_approval is True

    st = watches.get_watch_state(start_res.watch_id)
    assert st.status == "running"
    assert st.pane_id == "terminal_55"
    assert len(st.baseline_tree) == 40


def test_start_watch_run_continue_watch_id_profile_pinning(
    mock_environment, git_repo: Path, monkeypatch
):
    # 1. Start initial run with pinned profile
    start1 = start_watch_run(
        "Step 1",
        workspace=str(git_repo),
        profile="profile-A",
    )
    finalize_watch(start1.watch_id, exit_code=0)

    # 2. Continue with continue_watch_id
    selected_profiles = []

    def track_select(explicit_name=None):
        selected_profiles.append(explicit_name)
        return SimpleNamespace(name=explicit_name or "selected")

    monkeypatch.setattr(watches, "select_profile", track_select)

    start2 = start_watch_run(
        "Step 2 continue",
        workspace=str(git_repo),
        continue_watch_id=start1.watch_id,
    )

    assert start2.profile == "profile-A"
    assert selected_profiles == ["profile-A"]

    req2 = watches.get_watch_request(start2.watch_id)
    assert req2.continue_watch_id == start1.watch_id

    # 3. Conflicting profile is rejected
    with pytest.raises(ValueError, match="Cannot specify profile"):
        start_watch_run(
            "Step 3 conflicting",
            workspace=str(git_repo),
            profile="profile-B",
            continue_watch_id=start1.watch_id,
        )


def test_watch_status_and_monitor_reconciles_exit_signal(
    mock_environment, git_repo: Path
):
    start_res = start_watch_run("Test exit reconcile", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    st1 = get_watch_status(start_res.watch_id)
    assert st1.status == "running"

    (git_repo / "new_out.txt").write_text("finished work\n", encoding="utf-8")
    (watch_dir / ".exit").write_text("0\n", encoding="utf-8")

    st2 = get_watch_status(start_res.watch_id)
    assert st2.status == "completed"
    assert st2.exit_code == 0

    res_diff = get_watch_result(start_res.watch_id)
    assert "new_out.txt" in res_diff.diff
    assert res_diff.eof is True


def test_early_pane_closure_fails_watch(mock_environment, git_repo: Path, monkeypatch):
    start_res = start_watch_run("Test early close", workspace=str(git_repo))

    monkeypatch.setattr(watches, "is_pane_alive", lambda *a, **kw: False)

    st = get_watch_status(start_res.watch_id)
    assert st.status == "failed"
    assert "Pane closed" in (st.error or "")


def test_cancel_watch_run(mock_environment, git_repo: Path, monkeypatch):
    closed_panes = []
    monkeypatch.setattr(
        watches, "close_zellij_pane", lambda pane_id: closed_panes.append(pane_id)
    )

    start_res = start_watch_run("Test cancel", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    pty_log = watch_dir / "pty.log"
    pty_log.write_text("Partial execution output...", encoding="utf-8")

    status = cancel_watch_run(start_res.watch_id)
    assert status.status == "cancelled"
    assert closed_panes == ["terminal_42"]

    assert pty_log.read_text(encoding="utf-8") == "Partial execution output..."

    lease_file = watches._repo_lease_path(git_repo)
    assert not lease_file.exists()


def test_watch_log_and_result_chunking(mock_environment, git_repo: Path):
    start_res = start_watch_run("Chunk test", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    pty_log = watch_dir / "pty.log"
    pty_log.write_text("Line 1\nLine 2\nLine 3\n", encoding="utf-8")

    chunk1 = get_watch_log(start_res.watch_id, offset=0, limit=7)
    assert chunk1.content == "Line 1\n"
    assert chunk1.next_offset == 7
    assert chunk1.eof is False

    chunk2 = get_watch_log(start_res.watch_id, offset=7, limit=50)
    assert chunk2.content == "Line 2\nLine 3\n"
    assert chunk2.next_offset == 21
    assert chunk2.eof is False

    with pytest.raises(ValueError, match="Offset cannot be negative"):
        get_watch_log(start_res.watch_id, offset=-1)
    with pytest.raises(ValueError, match="Limit must be at least"):
        get_watch_log(start_res.watch_id, limit=2)
    with pytest.raises(ValueError, match="Limit cannot exceed"):
        get_watch_log(start_res.watch_id, limit=2000000)


def test_wait_watch_run(mock_environment, git_repo: Path):
    start_res = start_watch_run("Wait test", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    (watch_dir / ".exit").write_text("0\n", encoding="utf-8")

    st = wait_watch_run(start_res.watch_id, timeout=1.0)
    assert st.status == "completed"


# ── Bug fix tests ──────────────────────────────────────────────────────────────


def test_corrupt_exit_signal_fails_watch_in_status(mock_environment, git_repo: Path):
    """Issue #5: corrupt .exit must fail the watch, not default to exit_code=0."""
    start_res = start_watch_run("Corrupt exit test", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    # Write a non-integer (corrupted) exit signal
    (watch_dir / ".exit").write_text("NOT_A_NUMBER\n", encoding="utf-8")

    st = get_watch_status(start_res.watch_id)
    assert st.status == "failed"
    assert "corrupted" in (st.error or "").lower()


def test_corrupt_exit_signal_fails_watch_in_monitor(mock_environment, git_repo: Path):
    """Issue #5: monitor must fail watch on corrupt .exit, not succeed."""
    import magy.watches as rv

    start_res = start_watch_run("Monitor corrupt exit", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    (watch_dir / ".exit").write_text("bad!", encoding="utf-8")

    result = rv.run_watch_monitor(start_res.watch_id, poll_interval=0.0, timeout=1.0)
    assert result == 1

    st = get_watch_status(start_res.watch_id)
    assert st.status == "failed"
    assert "corrupted" in (st.error or "").lower()


def test_snapshot_failure_preserved_when_agy_exits_zero(
    mock_environment, git_repo: Path, monkeypatch
):
    """Issue #3: failed git snapshot must set failed, never completed."""
    import magy.watches as rv

    start_res = start_watch_run("Snapshot fail test", workspace=str(git_repo))

    # Simulate git snapshot raising during finalize
    def _raise_on_snapshot(*a, **kw):
        raise RuntimeError("git index error")

    monkeypatch.setattr(rv, "take_git_snapshot", _raise_on_snapshot)

    state = finalize_watch(start_res.watch_id, exit_code=0)
    assert state.status == "failed"
    assert state.error is not None
    assert "git" in state.error.lower() or "snapshot" in state.error.lower()


def test_lease_stub_state_prevents_race(git_repo: Path, tmp_path: Path, monkeypatch):
    """Issue #4: state.json stub written before lease so concurrent runner
    sees status=running and backs off."""
    import magy.watches as rv

    watch_id = "watch_aabbccddaabbccdd"
    watch_dir = rv.get_watch_dir(watch_id)
    watch_dir.mkdir(parents=True, exist_ok=True)

    # Simulate what start_watch_run does: write stub then acquire lease
    from magy.storage import atomic_write_json

    stub = {
        "watch_id": watch_id,
        "status": "running",
        "profile": "test",
        "workspace": str(git_repo),
        "repo_root": str(git_repo),
        "session": "",
        "baseline_tree": None,
        "baseline_commit": "abc123",
        "final_tree": None,
        "started_at": 0.0,
        "created_at": 0.0,
        "finished_at": None,
        "exit_code": None,
        "error": None,
        "pane_id": None,
        "runner_pid": None,
        "runner_create_time": None,
        "pty_log_path": None,
        "diff_path": None,
        "exit_signal_path": None,
    }
    atomic_write_json(watch_dir / "state.json", stub)
    acquire_repo_lease(git_repo, watch_id)

    # Now a second runner sees the running stub and cannot steal lease
    other_id = "watch_1234567890abcdef"
    with pytest.raises(ValueError, match="Another watched run is already active"):
        acquire_repo_lease(git_repo, other_id)

    release_repo_lease(git_repo, watch_id)


def test_runner_sets_env_vars_and_parses_stream_json(
    mock_environment, git_repo: Path, monkeypatch
):
    """Issue #2 + stream-json: runner sets MAGY_RUN_ID/WATCH_ID and parses events."""
    import io
    import os

    from magy.watch_runner import _render_event, run_watch_runner

    start_res = start_watch_run("Stream-json env test", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    captured_env = {}

    # Build fake stream-json output mimicking agy's real events
    _agent_usage = (
        '"usage":{"input_tokens":100,"output_tokens":5,'
        '"thinking_tokens":3,"cache_read_tokens":0,"total_tokens":105}'
    )
    _agent_step = (
        '{"event":"step_update","step_update":{"conversation_id":"abc123",'
        '"step_index":1,"state":"DONE","step_type":"agent_response",'
        f'"text_delta":"Hello World.\\n","duration_seconds":1.23,{_agent_usage}}}}}'
    )
    _result_line = (
        '{"event":"result","result":{"conversation_id":"abc123",'
        f'"status":"SUCCESS","response":"Hello World.\\n",'
        f'"duration_seconds":1.5,"num_turns":1,{_agent_usage}}}}}'
    )
    fake_ndjson = "\n".join(
        [
            '{"event":"init","conversation_id":"abc123","init":{"permission_mode":"always-proceed"}}',
            '{"event":"step_update","step_update":{"conversation_id":"abc123","step_index":0,"state":"DONE","step_type":"user_input"}}',
            _agent_step,
            _result_line,
            "",
        ]
    )

    class FakeProc:
        def __init__(self):
            self.stdout = io.StringIO(fake_ndjson)
            self.returncode = 0

        def wait(self):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    captured_cmds = []
    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        if any("magy.cli" in str(arg) for arg in cmd):
            captured_cmds.append(list(cmd))
            captured_env.update(os.environ.copy())
            return FakeProc()
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr("magy.watch_runner.subprocess.Popen", fake_popen)

    exit_code = run_watch_runner(str(watch_dir))

    # Exit code from result.status=SUCCESS → 0
    assert exit_code == 0

    # Env vars correctly set for _terminate_pid_tree verification
    assert captured_env.get("MAGY_WATCH_ID") == start_res.watch_id
    assert captured_env.get("MAGY_RUN_ID") == start_res.watch_id

    # conversation_id correctly captured into state.json
    st = watches.get_watch_state(start_res.watch_id)
    assert st.conversation_id == "abc123"

    # Continuation uses --conversation <id> instead of race-prone --continue
    finalize_watch(start_res.watch_id, exit_code=0)
    cont_res = start_watch_run(
        "Followup step",
        workspace=str(git_repo),
        continue_watch_id=start_res.watch_id,
    )
    cont_dir = get_watch_dir(cont_res.watch_id)
    cont_req = watches.get_watch_request(cont_res.watch_id)
    assert cont_req.conversation_id == "abc123"

    run_watch_runner(str(cont_dir))
    last_cmd = captured_cmds[-1]
    assert "--conversation" in last_cmd
    conv_idx = last_cmd.index("--conversation")
    assert last_cmd[conv_idx + 1] == "abc123"
    assert "--continue" not in last_cmd

    # pty.log written with raw NDJSON (machine-readable)
    log_content = (watch_dir / "pty.log").read_text(encoding="utf-8")
    assert '"event":"init"' in log_content
    assert '"event":"result"' in log_content

    # Human-readable rendering works correctly
    init_event = {
        "event": "init",
        "conversation_id": "abc123xyz",
        "init": {"permission_mode": "always-proceed"},
    }
    rendered = _render_event(init_event)
    assert rendered is not None
    assert "abc123xy" in rendered
    assert "always-proceed" in rendered

    agent_event = {
        "event": "step_update",
        "step_update": {
            "step_type": "agent_response",
            "state": "DONE",
            "text_delta": "Hello!\n",
            "duration_seconds": 2.5,
            "usage": {"output_tokens": 10, "thinking_tokens": 5},
        },
    }
    rendered = _render_event(agent_event)
    assert rendered is not None
    assert "Hello!" in rendered
    assert "2.5s" in rendered

    tool_active = {
        "event": "step_update",
        "step_update": {
            "step_type": "tool",
            "state": "ACTIVE",
            "tool_name": "run_command",
            "tool_info": {"parameters": {"CommandLine": "ls -la"}},
        },
    }
    rendered = _render_event(tool_active)
    assert rendered is not None
    assert "run_command" in rendered
    assert "ls -la" in rendered

    result_fail = {
        "event": "result",
        "result": {
            "status": "FAILURE",
            "duration_seconds": 5.0,
            "usage": {"total_tokens": 50},
            "denied_actions": [],
        },
    }
    rendered = _render_event(result_fail)
    assert rendered is not None
    assert "FAILURE" in rendered


def test_runner_failure_status_returns_nonzero(
    mock_environment, git_repo: Path, monkeypatch
):
    """result.status != SUCCESS → runner exits 1."""
    import io

    from magy.watch_runner import run_watch_runner

    start_res = start_watch_run("Failure status test", workspace=str(git_repo))
    watch_dir = get_watch_dir(start_res.watch_id)

    fake_ndjson = "\n".join(
        [
            '{"event":"result","result":{"status":"FAILURE","duration_seconds":1.0,"num_turns":1,"usage":{"total_tokens":10}}}',
            "",
        ]
    )

    class FakeProc:
        stdout = io.StringIO(fake_ndjson)
        returncode = 1

        def wait(self):
            return 1

    monkeypatch.setattr(
        "magy.watch_runner.subprocess.Popen", lambda *a, **kw: FakeProc()
    )

    exit_code = run_watch_runner(str(watch_dir))
    assert exit_code == 1


def test_start_watch_run_auto_approval_default_and_override(
    mock_environment, git_repo: Path, monkeypatch
):
    """watch_start defaults to auto_approval=True (--dangerously-skip-permissions).

    Can be explicitly overridden to False.
    """
    import io

    from magy.watch_runner import run_watch_runner

    fake_ndjson = "\n".join(
        [
            '{"event":"result","result":{"status":"SUCCESS","duration_seconds":1.0,"num_turns":1,"usage":{"total_tokens":10}}}',
            "",
        ]
    )

    class FakeProc:
        def __init__(self):
            self.stdout = io.StringIO(fake_ndjson)
            self.returncode = 0

        def wait(self):
            return 0

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    captured_cmds = []
    real_popen = subprocess.Popen

    def fake_popen(cmd, **kwargs):
        if any("magy.cli" in str(arg) for arg in cmd):
            captured_cmds.append(list(cmd))
            return FakeProc()
        return real_popen(cmd, **kwargs)

    monkeypatch.setattr("magy.watch_runner.subprocess.Popen", fake_popen)

    # 1. Default should be auto_approval=True
    start_default = start_watch_run("Default auto approval", workspace=str(git_repo))
    req_default = watches.get_watch_request(start_default.watch_id)
    assert req_default.auto_approval is True

    run_watch_runner(str(get_watch_dir(start_default.watch_id)))
    assert "--dangerously-skip-permissions" in captured_cmds[-1]
    finalize_watch(start_default.watch_id, exit_code=0)

    # 2. Explicit False should omit --dangerously-skip-permissions
    start_explicit_false = start_watch_run(
        "Explicit false", workspace=str(git_repo), auto_approval=False
    )
    req_explicit_false = watches.get_watch_request(start_explicit_false.watch_id)
    assert req_explicit_false.auto_approval is False

    run_watch_runner(str(get_watch_dir(start_explicit_false.watch_id)))
    assert "--dangerously-skip-permissions" not in captured_cmds[-1]
    finalize_watch(start_explicit_false.watch_id, exit_code=0)


def test_watch_mux_auto_precedence(git_repo: Path, monkeypatch, mock_environment):
    """mux='auto' chooses zellij when both session contexts are present."""
    monkeypatch.setenv("ZELLIJ", "1")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "z-session")
    monkeypatch.setenv("TMUX", "/tmp/tmux-sock,123,0")
    monkeypatch.setenv("TMUX_PANE", "%9")

    captured_cmds = []
    real_run = subprocess.run

    def fake_run(command, *args, **kwargs):
        if any("zellij" in str(arg) for arg in command):
            captured_cmds.append(list(command))
            return subprocess.CompletedProcess(command, 0, "terminal_1\n", "")
        if any("tmux" in str(arg) for arg in command):
            captured_cmds.append(list(command))
            return subprocess.CompletedProcess(command, 0, "%42\n", "")
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fake_run)

    # 1. Both contexts present -> picks zellij (precedence)
    start_res = start_watch_run("Auto precedence", workspace=str(git_repo))
    st = watches.get_watch_state(start_res.watch_id)
    assert st.mux == "zellij"
    assert any("zellij" in str(arg) for arg in captured_cmds[-1])
    assert "--near-current-pane" in captured_cmds[-1]
    assert "--no-focus" in captured_cmds[-1]
    finalize_watch(start_res.watch_id, exit_code=0)

    # 2. Only tmux present -> picks tmux
    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)

    start_tmux = start_watch_run("Auto tmux", workspace=str(git_repo))
    st_tmux = watches.get_watch_state(start_tmux.watch_id)
    assert st_tmux.mux == "tmux"
    assert st_tmux.mux_socket == "/tmp/tmux-sock"
    assert any("tmux" in str(arg) for arg in captured_cmds[-1])
    finalize_watch(start_tmux.watch_id, exit_code=0)

    # 3. Neither present -> raises RuntimeError
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    with pytest.raises(RuntimeError, match="active Zellij or tmux session"):
        start_watch_run("Auto neither", workspace=str(git_repo))


def test_watch_explicit_mux_selection_and_validation(
    git_repo: Path, monkeypatch, mock_environment
):
    """Explicit mux validates required session/binary and rejects invalid mux."""
    # 1. Invalid mux string rejected
    with pytest.raises(ValueError, match="Multiplexer 'screen' is not supported"):
        start_watch_run("Invalid mux", workspace=str(git_repo), mux="screen")

    # 2. mux='zellij' requires active zellij session
    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)
    with pytest.raises(RuntimeError, match="active Zellij session"):
        start_watch_run(
            "Explicit zellij missing session", workspace=str(git_repo), mux="zellij"
        )

    # 3. mux='zellij' requires zellij on PATH
    monkeypatch.setenv("ZELLIJ", "1")
    monkeypatch.setenv("ZELLIJ_SESSION_NAME", "z-sess")
    monkeypatch.setattr(
        watches.shutil,
        "which",
        lambda cmd: None if cmd == "zellij" else f"/usr/bin/{cmd}",
    )
    with pytest.raises(RuntimeError, match="requires Zellij installed"):
        start_watch_run(
            "Explicit zellij missing binary", workspace=str(git_repo), mux="zellij"
        )

    # 4. mux='tmux' requires active tmux session
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)
    monkeypatch.setattr(watches.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")
    with pytest.raises(RuntimeError, match="active tmux session"):
        start_watch_run(
            "Explicit tmux missing session", workspace=str(git_repo), mux="tmux"
        )

    # 5. mux='tmux' requires tmux on PATH
    monkeypatch.setenv("TMUX", "/tmp/sock,123,0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    monkeypatch.setattr(
        watches.shutil,
        "which",
        lambda cmd: None if cmd == "tmux" else f"/usr/bin/{cmd}",
    )
    with pytest.raises(RuntimeError, match="requires tmux installed"):
        start_watch_run(
            "Explicit tmux missing binary", workspace=str(git_repo), mux="tmux"
        )


def test_native_tmux_launch_target_and_quoting(
    git_repo: Path, tmp_path: Path, monkeypatch, mock_environment
):
    """Explicit tmux overrides Zellij, targets origin, and quotes runner paths."""
    socket_path = "/run/user/1000/socket,with,commas"
    monkeypatch.setenv("TMUX", f"{socket_path},9999,0")
    monkeypatch.setenv("TMUX_PANE", "%3")
    monkeypatch.setenv("MAGY_STATE_DIR", str(tmp_path / "state with 'quotes'"))

    captured_cmds = []
    real_run = subprocess.run

    def fake_subprocess_run(cmd, **kwargs):
        if Path(cmd[0]).name == "tmux":
            captured_cmds.append((cmd, kwargs))
            return subprocess.CompletedProcess(cmd, 0, "%77\n", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fake_subprocess_run)

    start_res = start_watch_run(
        "Refactor auth logic",
        workspace=str(git_repo),
        mux="tmux",
    )

    assert start_res.pane_id == "%77"
    assert len(captured_cmds) == 1
    cmd, kwargs = captured_cmds[0]

    # Command matches tmux -S socket split-window with originating target
    assert cmd[:4] == [
        "/usr/bin/tmux",
        "-S",
        socket_path,
        "split-window",
    ]
    assert "-h" in cmd
    assert "-d" in cmd
    assert "-P" in cmd
    assert "-F" in cmd
    f_idx = cmd.index("-F")
    assert cmd[f_idx + 1] == "#{pane_id}"
    t_idx = cmd.index("-t")
    assert cmd[t_idx + 1] == "%3"
    c_idx = cmd.index("-c")
    assert cmd[c_idx + 1] == str(git_repo.resolve())

    # Shell-quoted command
    last_arg = cmd[-1]
    watch_dir = get_watch_dir(start_res.watch_id)
    runner_path = str(watch_dir / "runner.sh")
    assert shlex.split(last_arg) == ["bash", runner_path]

    # State persists mux and socket
    st = watches.get_watch_state(start_res.watch_id)
    assert st.mux == "tmux"
    assert st.mux_socket == socket_path
    assert st.pane_id == "%77"
    assert len(st.baseline_tree) == 40


def test_custom_mux_cmd_unchanged_and_receives_no_flags(
    git_repo: Path, monkeypatch, mock_environment
):
    """Custom mux_cmd receives no flags, preserving tmux -d -t %id command."""
    popen_calls = []

    class FakeProc:
        pid = 12345

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

    real_popen = subprocess.Popen

    def fake_popen(cmd, *args, **kwargs):
        if any("tmux" in str(arg) for arg in cmd):
            popen_calls.append((cmd, kwargs))
            return FakeProc()
        return real_popen(cmd, *args, **kwargs)

    monkeypatch.setattr(watches.subprocess, "Popen", fake_popen)

    # tmux-style custom command with -d -t %id
    custom_cmd = ["tmux", "split-window", "-d", "-t", "%12", "{cmd}"]
    start_res = start_watch_run(
        "Custom mux task",
        workspace=str(git_repo),
        mux_cmd=custom_cmd,
    )

    assert start_res.pane_id == "terminal_12345"
    assert len(popen_calls) == 1
    cmd, kwargs = popen_calls[0]

    # Verify no Zellij flags were injected
    assert "--near-current-pane" not in cmd
    assert "--no-focus" not in cmd
    assert "--floating" not in cmd
    assert "--name" not in cmd

    # Verify exact substitution of {cmd}
    watch_dir = get_watch_dir(start_res.watch_id)
    runner_sh = str(watch_dir / "runner.sh")
    assert cmd == ["tmux", "split-window", "-d", "-t", "%12", f"bash {runner_sh}"]
    assert kwargs["cwd"] == str(git_repo.resolve())

    st = watches.get_watch_state(start_res.watch_id)
    assert st.mux == "custom"
    assert st.mux_socket is None


def test_persisted_state_backward_compatibility(tmp_path: Path):
    """Legacy state without mux or mux_socket defaults to zellij and None."""
    legacy_data = {
        "watch_id": "watch_legacy123456",
        "status": "completed",
        "profile": "default",
        "workspace": str(tmp_path),
        "pane_id": "terminal_10",
    }
    st = WatchRunState.from_dict(legacy_data)
    assert st.mux == "zellij"
    assert st.mux_socket is None


def test_tmux_liveness_dead_missing_pane(monkeypatch):
    """is_tmux_pane_alive checks pane alive (0), dead (1), missing, or invalid ID."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        t_idx = cmd.index("-t")
        target = cmd[t_idx + 1]
        if target == "%10":
            return subprocess.CompletedProcess(cmd, 0, "0\n", "")  # alive
        elif target == "%20":
            return subprocess.CompletedProcess(cmd, 0, "1\n", "")  # dead
        elif target == "%30":
            return subprocess.CompletedProcess(
                cmd, 1, "", "can't find pane: %30\n"
            )  # missing
        return subprocess.CompletedProcess(cmd, 1, "", "no server running")

    monkeypatch.setattr(watches.subprocess, "run", fake_run)
    monkeypatch.setattr(watches.shutil, "which", lambda cmd: "/usr/bin/tmux")

    # Alive pane
    assert watches.is_tmux_pane_alive("%10", mux_socket="/tmp/s.sock") is True
    assert "-S" in calls[-1] and "/tmp/s.sock" in calls[-1]
    assert "display-message" in calls[-1]

    # Dead pane
    assert watches.is_tmux_pane_alive("%20", mux_socket="/tmp/s.sock") is False

    # Missing pane
    assert watches.is_tmux_pane_alive("%30", mux_socket="/tmp/s.sock") is False

    # Invalid / non-tmux pane ID returns False without executing tmux
    call_count = len(calls)
    assert watches.is_tmux_pane_alive("terminal_42", mux_socket="/tmp/s.sock") is False
    assert watches.is_tmux_pane_alive("", mux_socket="/tmp/s.sock") is False
    assert len(calls) == call_count


def test_close_tmux_pane_only_stable_native_id(monkeypatch):
    """close_tmux_pane only kills valid %N pane IDs using persisted socket."""
    calls = []

    def fake_run(cmd, **kwargs):
        calls.append(list(cmd))
        return subprocess.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(watches.subprocess, "run", fake_run)
    monkeypatch.setattr(watches.shutil, "which", lambda cmd: "/usr/bin/tmux")

    # Valid %N pane
    watches.close_tmux_pane("%42", mux_socket="/tmp/tmux.sock")
    assert len(calls) == 1
    assert calls[0] == [
        "/usr/bin/tmux",
        "-S",
        "/tmp/tmux.sock",
        "kill-pane",
        "-t",
        "%42",
    ]

    # Invalid IDs must NOT invoke kill-pane
    watches.close_tmux_pane("terminal_42", mux_socket="/tmp/tmux.sock")
    watches.close_tmux_pane("non_numeric", mux_socket="/tmp/tmux.sock")
    watches.close_tmux_pane("", mux_socket="/tmp/tmux.sock")
    assert len(calls) == 1


def test_monitor_finalize_cancel_route_by_persisted_state(
    git_repo: Path, monkeypatch, mock_environment
):
    """Monitor, finalize, and cancel route by persisted state, not caller env."""
    monkeypatch.setenv("TMUX", "/tmp/persisted.sock,123,0")
    monkeypatch.setenv("TMUX_PANE", "%5")
    monkeypatch.delenv("ZELLIJ", raising=False)
    monkeypatch.delenv("ZELLIJ_SESSION_NAME", raising=False)

    killed_panes = []
    liveness_queries = []

    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if Path(cmd[0]).name == "tmux":
            if "split-window" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "%88\n", "")
            if "kill-pane" in cmd:
                killed_panes.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, "", "")
            if "display-message" in cmd:
                liveness_queries.append(list(cmd))
                return subprocess.CompletedProcess(cmd, 0, "0\n", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fake_run)

    start_res = start_watch_run(
        "Persisted routing", workspace=str(git_repo), mux="tmux"
    )
    assert start_res.pane_id == "%88"

    # Now clear TMUX from environment to simulate outside/monitor caller
    monkeypatch.delenv("TMUX", raising=False)
    monkeypatch.delenv("TMUX_PANE", raising=False)

    # Status check checks liveness using persisted tmux backend and socket
    watches.get_watch_status(start_res.watch_id)
    assert len(liveness_queries) == 1
    assert "-S" in liveness_queries[0]
    assert "/tmp/persisted.sock" in liveness_queries[0]
    assert "%88" in liveness_queries[0]

    # Cancellation kills pane using persisted tmux backend and socket
    watches.cancel_watch_run(start_res.watch_id)
    assert len(killed_panes) == 1
    assert killed_panes[0] == [
        "/usr/bin/tmux",
        "-S",
        "/tmp/persisted.sock",
        "kill-pane",
        "-t",
        "%88",
    ]


def test_launch_failure_releases_lease(git_repo: Path, monkeypatch, mock_environment):
    """Failure during pane launch releases repository lease and marks watch failed."""

    real_run = subprocess.run

    def fail_subprocess(cmd, **kwargs):
        if Path(cmd[0]).name == "zellij":
            return subprocess.CompletedProcess(cmd, 1, "", "cannot open window")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fail_subprocess)

    with pytest.raises(
        RuntimeError, match="Zellij could not create a watched Magy pane"
    ):
        start_watch_run("Failing launch", workspace=str(git_repo))

    lease_file = watches._repo_lease_path(git_repo)
    assert not lease_file.exists()


@pytest.mark.parametrize(
    ("context", "pane"),
    [
        ("invalid", "%1"),
        (",123,0", "%1"),
        ("/tmp/sock,not-a-pid,0", "%1"),
        ("/tmp/sock,123,0", "origin:0"),
        ("/tmp/sock,123,0", "%١"),
    ],
)
def test_tmux_rejects_invalid_origin_context(context, pane, monkeypatch):
    monkeypatch.setenv("TMUX", context)
    monkeypatch.setenv("TMUX_PANE", pane)
    monkeypatch.setattr(watches.shutil, "which", lambda _: "/usr/bin/tmux")
    with pytest.raises(RuntimeError, match="valid TMUX and TMUX_PANE"):
        watches.validate_tmux_environment()


@pytest.mark.parametrize(
    ("returncode", "stdout"), [(1, ""), (0, ""), (0, "terminal_42\n")]
)
def test_tmux_launch_failure_releases_lease(
    returncode, stdout, git_repo, monkeypatch, mock_environment
):
    monkeypatch.setenv("TMUX", "/tmp/sock,123,0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    real_run = subprocess.run

    def fake_run(cmd, **kwargs):
        if Path(cmd[0]).name == "tmux":
            return subprocess.CompletedProcess(cmd, returncode, stdout, "private error")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fake_run)
    with pytest.raises(RuntimeError, match="Tmux") as exc:
        start_watch_run("Failure", workspace=str(git_repo), mux="tmux")
    assert "private error" not in str(exc.value)
    assert not watches._repo_lease_path(git_repo).exists()


@pytest.mark.parametrize("early_close", [False, True])
def test_tmux_monitor_and_finalize_use_saved_socket(
    early_close, git_repo, monkeypatch, mock_environment
):
    monkeypatch.setenv("TMUX", "/tmp/origin.sock,123,0")
    monkeypatch.setenv("TMUX_PANE", "%1")
    real_run = subprocess.run
    calls = []

    def fake_run(cmd, **kwargs):
        if Path(cmd[0]).name == "tmux":
            calls.append(cmd)
            if "split-window" in cmd:
                return subprocess.CompletedProcess(cmd, 0, "%88\n", "")
            # Missing panes can produce successful but empty display-message output.
            return subprocess.CompletedProcess(cmd, 0, "", "")
        return real_run(cmd, **kwargs)

    monkeypatch.setattr(watches.subprocess, "run", fake_run)
    result = start_watch_run("Monitor", workspace=str(git_repo), mux="tmux")
    monkeypatch.setenv("TMUX", "/tmp/unrelated.sock,456,1")
    monkeypatch.setenv("TMUX_PANE", "%99")
    if not early_close:
        (get_watch_dir(result.watch_id) / ".exit").write_text("0\n", encoding="utf-8")
    assert watches.run_watch_monitor(result.watch_id, poll_interval=0, timeout=1) == 0
    state = watches.get_watch_state(result.watch_id)
    assert state.status == ("failed" if early_close else "completed")
    assert not watches._repo_lease_path(git_repo).exists()
    assert calls[-1] == [
        "/usr/bin/tmux",
        "-S",
        "/tmp/origin.sock",
        "kill-pane",
        "-t",
        "%88",
    ]
    assert all(cmd[2] == "/tmp/origin.sock" for cmd in calls)
    assert any("display-message" in cmd for cmd in calls) == early_close


def test_custom_launcher_pid_is_not_pane_liveness(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Custom launcher PID must not be checked as a pane")

    monkeypatch.setattr(watches.os, "kill", unexpected)
    monkeypatch.setattr(watches.subprocess, "run", unexpected)
    assert watches.is_pane_alive("terminal_12345", mux="custom")
    watches.close_pane("terminal_12345", mux="custom")


def test_tmux_missing_saved_socket_never_uses_caller_server(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Tmux operations must not fall back to caller's server")

    monkeypatch.setattr(watches.subprocess, "run", unexpected)
    assert not watches.is_tmux_pane_alive("%1")
    watches.close_tmux_pane("%1")
