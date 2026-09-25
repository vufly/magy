import json
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

import magy.reviews as reviews
from magy.reviews import (
    ReviewRunStart,
    ReviewRunState,
    acquire_repo_lease,
    cancel_review_run,
    compute_git_diff,
    finalize_review,
    get_review_dir,
    get_review_log,
    get_review_result,
    get_review_status,
    release_repo_lease,
    start_review_run,
    take_git_snapshot,
    validate_git_repository,
    wait_review_run,
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
    monkeypatch.setattr(reviews.shutil, "which", lambda cmd: f"/usr/bin/{cmd}")

    config = SimpleNamespace(agy_cmd=None, agy_resolver=None)
    monkeypatch.setattr(
        reviews,
        "load_config_result",
        lambda: SimpleNamespace(error=None, config=config),
    )
    monkeypatch.setattr(
        reviews,
        "resolve_agy_executable",
        lambda **kwargs: ("/usr/bin/agy", None),
    )
    monkeypatch.setattr(
        reviews,
        "get_agy_capabilities",
        lambda executable: {
            "supports_auto_approval": True,
            "supports_add_dir": True,
        },
    )
    monkeypatch.setattr(
        reviews,
        "select_profile",
        lambda explicit_name: SimpleNamespace(name=explicit_name or "default-p"),
    )
    monkeypatch.setattr(
        reviews,
        "spawn_detached_monitor",
        lambda review_id: SimpleNamespace(pid=9999),
    )

    real_run = subprocess.run

    def fake_run(cmd, *args, **kwargs):
        if any("zellij" in str(arg) for arg in cmd):
            return subprocess.CompletedProcess(cmd, 0, "terminal_42\n", "")
        return real_run(cmd, *args, **kwargs)

    monkeypatch.setattr(reviews.subprocess, "run", fake_run)


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

    # 3. Simulate Agy run edits + user manual commit during review
    agy_new = git_repo / "agy_created.txt"
    agy_new.write_text("created by agy\n", encoding="utf-8")

    readme.write_text(
        "# Test Repo\nPre-existing dirty edit\n+ Agy new line\n", encoding="utf-8"
    )

    manual = git_repo / "manual.txt"
    manual.write_text("user manual commit during review\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(git_repo), "add", "manual.txt"], check=True)
    subprocess.run(
        ["git", "-C", str(git_repo), "commit", "-m", "user commit during review"],
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
    assert "+user manual commit during review" in patch

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
    rev1 = "rev_1111111111111111"
    rev2 = "rev_2222222222222222"

    dir1 = get_review_dir(rev1)
    dir1.mkdir(parents=True, exist_ok=True)
    dir2 = get_review_dir(rev2)
    dir2.mkdir(parents=True, exist_ok=True)

    st1 = ReviewRunState(
        review_id=rev1,
        status="running",
        repo_root=str(git_repo),
        workspace=str(git_repo),
    )
    (dir1 / "state.json").write_text(json.dumps(st1.to_dict()), encoding="utf-8")

    acquire_repo_lease(git_repo, rev1)

    # Second active run on same repo rejected
    with pytest.raises(
        ValueError, match="Another reviewed run is already active for this repository"
    ):
        acquire_repo_lease(git_repo, rev2)

    # Different repo allowed concurrently
    other_repo = tmp_path / "other_repo"
    other_repo.mkdir()
    acquire_repo_lease(other_repo, rev2)
    release_repo_lease(other_repo, rev2)

    # Releasing rev1 lease allows rev2
    release_repo_lease(git_repo, rev1)
    acquire_repo_lease(git_repo, rev2)
    release_repo_lease(git_repo, rev2)


def test_start_review_run_builds_command_and_runner_script(
    mock_environment, git_repo: Path, monkeypatch
):
    calls = []
    real_run = subprocess.run

    def fake_subprocess_run(command, **kwargs):
        if any("zellij" in str(arg) for arg in command):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 0, "terminal_55\n", "")
        return real_run(command, **kwargs)

    monkeypatch.setattr(reviews.subprocess, "run", fake_subprocess_run)

    start_res = start_review_run(
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

    assert isinstance(start_res, ReviewRunStart)
    assert start_res.pane_id == "terminal_55"
    assert start_res.profile == "dev-profile"
    assert start_res.workspace == str(git_repo.resolve())

    # Verify Zellij launch command
    zellij_cmd, _ = calls[-1]
    assert zellij_cmd[:7] == [
        "/usr/bin/zellij",
        "run",
        "--floating",
        "--name",
        f"magy-task-{start_res.review_id}",
        "--cwd",
        str(git_repo.resolve()),
    ]
    assert zellij_cmd[8:12] == [
        "bash",
        "-c",
        'exec bash "$1"',
        "magy-review",
    ]

    # Verify runner script was written with mode 0o700 and no interpolated prompt
    review_dir = get_review_dir(start_res.review_id)
    runner_sh = review_dir / "runner.sh"
    assert runner_sh.exists()
    assert (runner_sh.stat().st_mode & 0o777) == 0o700
    script_text = runner_sh.read_text(encoding="utf-8")
    assert "Refactor parser" not in script_text
    assert "magy.review_runner" in script_text
    assert ".exit" in script_text

    req = reviews.get_review_request(start_res.review_id)
    assert req.prompt == "Refactor parser"
    assert req.model == "gemini-3.8-flash"
    assert req.agent == "code-reviewer"
    assert req.auto_approval is True

    st = reviews.get_review_state(start_res.review_id)
    assert st.status == "running"
    assert st.pane_id == "terminal_55"
    assert len(st.baseline_tree) == 40


def test_start_review_run_continue_review_id_profile_pinning(
    mock_environment, git_repo: Path, monkeypatch
):
    # 1. Start initial run with pinned profile
    start1 = start_review_run(
        "Step 1",
        workspace=str(git_repo),
        profile="profile-A",
    )
    finalize_review(start1.review_id, exit_code=0)

    # 2. Continue with continue_review_id
    selected_profiles = []

    def track_select(explicit_name=None):
        selected_profiles.append(explicit_name)
        return SimpleNamespace(name=explicit_name or "selected")

    monkeypatch.setattr(reviews, "select_profile", track_select)

    start2 = start_review_run(
        "Step 2 continue",
        workspace=str(git_repo),
        continue_review_id=start1.review_id,
    )

    assert start2.profile == "profile-A"
    assert selected_profiles == ["profile-A"]

    req2 = reviews.get_review_request(start2.review_id)
    assert req2.continue_review_id == start1.review_id

    # 3. Conflicting profile is rejected
    with pytest.raises(ValueError, match="Cannot specify profile"):
        start_review_run(
            "Step 3 conflicting",
            workspace=str(git_repo),
            profile="profile-B",
            continue_review_id=start1.review_id,
        )


def test_review_status_and_monitor_reconciles_exit_signal(
    mock_environment, git_repo: Path
):
    start_res = start_review_run("Test exit reconcile", workspace=str(git_repo))
    review_dir = get_review_dir(start_res.review_id)

    st1 = get_review_status(start_res.review_id)
    assert st1.status == "running"

    (git_repo / "new_out.txt").write_text("finished work\n", encoding="utf-8")
    (review_dir / ".exit").write_text("0\n", encoding="utf-8")

    st2 = get_review_status(start_res.review_id)
    assert st2.status == "completed"
    assert st2.exit_code == 0

    res_diff = get_review_result(start_res.review_id)
    assert "new_out.txt" in res_diff.diff
    assert res_diff.eof is True


def test_early_pane_closure_fails_review(mock_environment, git_repo: Path, monkeypatch):
    start_res = start_review_run("Test early close", workspace=str(git_repo))

    monkeypatch.setattr(reviews, "is_pane_alive", lambda pane_id: False)

    st = get_review_status(start_res.review_id)
    assert st.status == "failed"
    assert "Pane closed" in (st.error or "")


def test_cancel_review_run(mock_environment, git_repo: Path, monkeypatch):
    closed_panes = []
    monkeypatch.setattr(
        reviews, "close_zellij_pane", lambda pane_id: closed_panes.append(pane_id)
    )

    start_res = start_review_run("Test cancel", workspace=str(git_repo))
    review_dir = get_review_dir(start_res.review_id)

    pty_log = review_dir / "pty.log"
    pty_log.write_text("Partial execution output...", encoding="utf-8")

    status = cancel_review_run(start_res.review_id)
    assert status.status == "cancelled"
    assert closed_panes == ["terminal_42"]

    assert pty_log.read_text(encoding="utf-8") == "Partial execution output..."

    lease_file = reviews._repo_lease_path(git_repo)
    assert not lease_file.exists()


def test_review_log_and_result_chunking(mock_environment, git_repo: Path):
    start_res = start_review_run("Chunk test", workspace=str(git_repo))
    review_dir = get_review_dir(start_res.review_id)

    pty_log = review_dir / "pty.log"
    pty_log.write_text("Line 1\nLine 2\nLine 3\n", encoding="utf-8")

    chunk1 = get_review_log(start_res.review_id, offset=0, limit=7)
    assert chunk1.content == "Line 1\n"
    assert chunk1.next_offset == 7
    assert chunk1.eof is False

    chunk2 = get_review_log(start_res.review_id, offset=7, limit=50)
    assert chunk2.content == "Line 2\nLine 3\n"
    assert chunk2.next_offset == 21
    assert chunk2.eof is False

    with pytest.raises(ValueError, match="Offset cannot be negative"):
        get_review_log(start_res.review_id, offset=-1)
    with pytest.raises(ValueError, match="Limit must be at least"):
        get_review_log(start_res.review_id, limit=2)
    with pytest.raises(ValueError, match="Limit cannot exceed"):
        get_review_log(start_res.review_id, limit=2000000)


def test_wait_review_run(mock_environment, git_repo: Path):
    start_res = start_review_run("Wait test", workspace=str(git_repo))
    review_dir = get_review_dir(start_res.review_id)

    (review_dir / ".exit").write_text("0\n", encoding="utf-8")

    st = wait_review_run(start_res.review_id, timeout=1.0)
    assert st.status == "completed"


# ── Bug fix tests ──────────────────────────────────────────────────────────────


def test_corrupt_exit_signal_fails_review_in_status(mock_environment, git_repo: Path):
    """Issue #5: corrupt .exit must fail the review, not default to exit_code=0."""
    start_res = start_review_run("Corrupt exit test", workspace=str(git_repo))
    review_dir = get_review_dir(start_res.review_id)

    # Write a non-integer (corrupted) exit signal
    (review_dir / ".exit").write_text("NOT_A_NUMBER\n", encoding="utf-8")

    st = get_review_status(start_res.review_id)
    assert st.status == "failed"
    assert "corrupted" in (st.error or "").lower()


def test_corrupt_exit_signal_fails_review_in_monitor(mock_environment, git_repo: Path):
    """Issue #5: monitor must fail review on corrupt .exit, not succeed."""
    import magy.reviews as rv

    start_res = start_review_run("Monitor corrupt exit", workspace=str(git_repo))
    review_dir = get_review_dir(start_res.review_id)

    (review_dir / ".exit").write_text("bad!", encoding="utf-8")

    result = rv.run_review_monitor(start_res.review_id, poll_interval=0.0, timeout=1.0)
    assert result == 1

    st = get_review_status(start_res.review_id)
    assert st.status == "failed"
    assert "corrupted" in (st.error or "").lower()


def test_snapshot_failure_preserved_when_agy_exits_zero(
    mock_environment, git_repo: Path, monkeypatch
):
    """Issue #3: failed git snapshot must set failed, never completed."""
    import magy.reviews as rv

    start_res = start_review_run("Snapshot fail test", workspace=str(git_repo))

    # Simulate git snapshot raising during finalize
    def _raise_on_snapshot(*a, **kw):
        raise RuntimeError("git index error")

    monkeypatch.setattr(rv, "take_git_snapshot", _raise_on_snapshot)

    state = finalize_review(start_res.review_id, exit_code=0)
    assert state.status == "failed"
    assert state.error is not None
    assert "git" in state.error.lower() or "snapshot" in state.error.lower()


def test_lease_stub_state_prevents_race(git_repo: Path, tmp_path: Path, monkeypatch):
    """Issue #4: state.json stub written before lease so concurrent runner
    sees status=running and backs off."""
    import magy.reviews as rv

    review_id = "rev_aabbccddaabbccdd"
    review_dir = rv.get_review_dir(review_id)
    review_dir.mkdir(parents=True, exist_ok=True)

    # Simulate what start_review_run does: write stub then acquire lease
    from magy.storage import atomic_write_json

    stub = {
        "review_id": review_id,
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
    atomic_write_json(review_dir / "state.json", stub)
    acquire_repo_lease(git_repo, review_id)

    # Now a second runner sees the running stub and cannot steal lease
    other_id = "rev_1234567890abcdef"
    with pytest.raises(ValueError, match="Another reviewed run is already active"):
        acquire_repo_lease(git_repo, other_id)

    release_repo_lease(git_repo, review_id)


def test_cancel_runner_env_allows_pid_tree_verify(
    mock_environment, git_repo: Path, monkeypatch
):
    """Issue #2: runner sets MAGY_RUN_ID=review_id so _terminate_pid_tree can verify."""
    import os

    from magy.review_runner import run_review_runner

    start_res = start_review_run("Cancel env test", workspace=str(git_repo))
    review_dir = get_review_dir(start_res.review_id)

    # Patch runner to capture env without actually spawning agy
    captured_env = {}

    def fake_spawn(cmd, master_read=None):
        captured_env.update(os.environ.copy())
        return 0  # os.waitstatus_to_exitcode(0) is 0

    def fake_waitstatus(status):
        return 0

    import pty as pty_mod

    monkeypatch.setattr(pty_mod, "spawn", fake_spawn)
    monkeypatch.setattr(os, "waitstatus_to_exitcode", fake_waitstatus)

    run_review_runner(str(review_dir))

    assert captured_env.get("MAGY_REVIEW_ID") == start_res.review_id
    assert captured_env.get("MAGY_RUN_ID") == start_res.review_id
