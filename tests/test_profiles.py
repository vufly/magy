import concurrent.futures
import os
import shutil
import signal
import stat
import sys
import time
from pathlib import Path

import pytest

from magy.cli import main
from magy.profiles import (
    PROTECTED_ENV_VARS,
    add_profile,
    build_profile_env,
    ensure_profile_layout,
    get_profile,
    get_profile_dir,
    get_profile_home_dir,
    run_in_profile,
    validate_profile_layout,
)


def test_ensure_profile_layout(tmp_path: Path):
    p_dir, p_home = ensure_profile_layout("test-p1")
    assert p_dir.exists()
    assert p_home.exists()
    gemini_dir = p_home / ".gemini"
    assert gemini_dir.exists()

    if os.name != "nt":
        assert stat.S_IMODE(p_dir.stat().st_mode) == 0o700
        assert stat.S_IMODE(p_home.stat().st_mode) == 0o700
        assert stat.S_IMODE(gemini_dir.stat().st_mode) == 0o700


def test_ensure_profile_layout_invalid_name():
    with pytest.raises(ValueError):
        ensure_profile_layout("invalid/name")
    with pytest.raises(ValueError):
        ensure_profile_layout("invalid\nname")


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_ensure_profile_layout_rejects_symlink_profile_dir(tmp_path: Path):
    external_dir = tmp_path / "external_target"
    external_dir.mkdir()

    p_dir = get_profile_dir("symlink-profile")
    p_dir.symlink_to(external_dir)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        ensure_profile_layout("symlink-profile")


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_ensure_profile_layout_rejects_symlink_home_dir(tmp_path: Path):
    p_dir = get_profile_dir("symlink-home")
    p_dir.mkdir(parents=True, exist_ok=True)
    external_home = tmp_path / "fake_real_home"
    external_home.mkdir()

    p_home = p_dir / "home"
    p_home.symlink_to(external_home)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        ensure_profile_layout("symlink-home")


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_ensure_profile_layout_rejects_symlink_gemini_dir(tmp_path: Path):
    p_dir = get_profile_dir("symlink-gemini")
    p_dir.mkdir(parents=True, exist_ok=True)
    p_home = p_dir / "home"
    p_home.mkdir(parents=True, exist_ok=True)

    external_gemini = tmp_path / "fake_gemini"
    external_gemini.mkdir()

    gemini_dir = p_home / ".gemini"
    gemini_dir.symlink_to(external_gemini)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        ensure_profile_layout("symlink-gemini")


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_ensure_profile_layout_rejects_symlink_profiles_root(tmp_path: Path):
    from magy.config import get_data_dir

    data_dir = get_data_dir()
    external_dir = tmp_path / "external_profiles_root"
    external_dir.mkdir()

    profiles_link = data_dir / "profiles"
    if profiles_link.exists():
        if profiles_link.is_dir() and not profiles_link.is_symlink():
            import shutil

            shutil.rmtree(profiles_link)
        else:
            profiles_link.unlink()
    profiles_link.symlink_to(external_dir)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        ensure_profile_layout("symlink-root-profile")


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_ensure_profile_layout_rejects_symlink_credential_subtree(tmp_path: Path):
    add_profile("symlink-cred", kind="managed")
    p_home = get_profile_home_dir("symlink-cred")
    gemini_dir = p_home / ".gemini"
    external_creds = tmp_path / "external_creds"
    external_creds.mkdir()

    cli_dir = gemini_dir / "antigravity-cli"
    cli_dir.symlink_to(external_creds)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        validate_profile_layout("symlink-cred")

    with pytest.raises(ValueError, match="cannot be a symlink"):
        ensure_profile_layout("symlink-cred")

    with pytest.raises(ValueError, match="cannot be a symlink"):
        run_in_profile("symlink-cred", ["models"])


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_profile_layout_rejects_real_home_symlink(tmp_path: Path):
    real_home = tmp_path / "user_real_home"
    real_gemini = real_home / ".gemini"
    real_gemini.mkdir(parents=True)

    add_profile("real-home-symlink", kind="managed")
    p_home = get_profile_home_dir("real-home-symlink")
    gemini_dir = p_home / ".gemini"
    shutil.rmtree(gemini_dir)
    gemini_dir.symlink_to(real_gemini)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        validate_profile_layout("real-home-symlink")

    with pytest.raises(ValueError, match="cannot be a symlink"):
        run_in_profile("real-home-symlink", ["models"])


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_profile_layout_rejects_cross_profile_symlink():
    add_profile("profile-alpha", kind="managed")
    add_profile("profile-beta", kind="managed")
    p_home_beta = get_profile_home_dir("profile-beta")

    alpha_gemini = get_profile_home_dir("profile-alpha") / ".gemini"
    beta_gemini = p_home_beta / ".gemini"
    shutil.rmtree(beta_gemini)
    beta_gemini.symlink_to(alpha_gemini)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        validate_profile_layout("profile-beta")

    with pytest.raises(ValueError, match="cannot be a symlink"):
        run_in_profile("profile-beta", ["models"])


def test_build_profile_env_posix():
    base_env = {
        "HOME": "/original/user/home",
        "PATH": "/usr/bin:/bin",
        "SSH_AUTH_SOCK": "/tmp/ssh.sock",
        "CUSTOM_VAR": "keep-me",
    }
    env = build_profile_env("worker-1", base_env=base_env, os_name="posix")

    expected_home = str(get_profile_home_dir("worker-1").resolve())
    assert env["HOME"] == expected_home
    assert env["MAGY_REAL_HOME"] == "/original/user/home"
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert env["MAGY_PROFILE"] == "worker-1"
    assert env["PATH"] == "/usr/bin:/bin"
    assert env["SSH_AUTH_SOCK"] == "/tmp/ssh.sock"
    assert env["CUSTOM_VAR"] == "keep-me"


def test_build_profile_env_parent_unmutated():
    before = dict(os.environ)
    build_profile_env("worker-immut", os_name="posix")
    after = dict(os.environ)
    assert before == after


def test_build_profile_env_windows_drive_letter():
    base_env = {
        "USERPROFILE": r"C:\Users\Original",
        "HOME": r"C:\Users\Original",
        "PATH": r"C:\Windows;C:\Windows\System32",
    }
    fake_win_home = r"D:\Profiles\worker-win\home"
    env = build_profile_env(
        "worker-win",
        base_env=base_env,
        os_name="nt",
        home_override=fake_win_home,
    )

    assert env["HOME"] == fake_win_home
    assert env["USERPROFILE"] == fake_win_home
    assert env["HOMEDRIVE"] == "D:"
    assert env["HOMEPATH"] == r"\Profiles\worker-win\home"
    assert env["MAGY_REAL_HOME"] == r"C:\Users\Original"
    assert env["AGY_CLI_DISABLE_AUTO_UPDATE"] == "true"
    assert env["MAGY_PROFILE"] == "worker-win"


def test_build_profile_env_windows_unc():
    base_env = {
        "USERPROFILE": r"\\fileserver\share\users\alice",
        "HOME": r"\\fileserver\share\users\alice",
    }
    fake_unc_home = r"\\fileserver\share\magy\profiles\unc-profile\home"
    env = build_profile_env(
        "unc-profile",
        base_env=base_env,
        os_name="nt",
        home_override=fake_unc_home,
    )

    assert env["HOME"] == fake_unc_home
    assert env["USERPROFILE"] == fake_unc_home
    assert env["HOMEDRIVE"] == r"\\fileserver\share"
    assert env["HOMEPATH"] == r"\magy\profiles\unc-profile\home"


@pytest.mark.parametrize("protected_key", sorted(PROTECTED_ENV_VARS))
def test_run_in_profile_protected_env_overrides_rejected(
    protected_key, fake_agy, monkeypatch
):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    add_profile("sec-profile", kind="managed")

    with pytest.raises(
        ValueError, match="Cannot override protected profile environment variables"
    ):
        run_in_profile(
            "sec-profile",
            ["models"],
            env_overrides={protected_key: "/injected/path"},
        )


def test_run_in_profile_respects_config_agy_cmd(fake_agy, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text(f'{{"agy_cmd": "{fake_agy.executable}"}}', encoding="utf-8")

    add_profile("cfg-profile", kind="managed")
    ret = run_in_profile("cfg-profile", ["models"])
    assert ret == 0

    invocations = fake_agy.get_invocations()
    assert len(invocations) == 1
    assert invocations[0]["args"] == ["models"]


def test_run_in_profile_invalid_config_fails_cleanly(fake_agy, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('{"agy_cmd": "/nonexistent/path/to/agy"}', encoding="utf-8")

    add_profile("bad-cfg", kind="managed")
    with pytest.raises(FileNotFoundError, match="Agy executable invalid"):
        run_in_profile("bad-cfg", ["models"])


def test_stage2_persistent_fake_accounts(fake_agy, monkeypatch, tmp_path: Path):
    """Stage 2 verification: persistent account alias markers in profile homes."""
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    add_profile("profile-a", kind="managed")
    add_profile("profile-b", kind="managed")

    # 1. Profile A and Profile B record different aliases
    ret_a = run_in_profile("profile-a", ["--record-alias", "account-A"])
    assert ret_a == 0

    ret_b = run_in_profile("profile-b", ["--record-alias", "account-B"])
    assert ret_b == 0

    marker_a = get_profile_home_dir("profile-a") / ".gemini" / "account_alias.txt"
    marker_b = get_profile_home_dir("profile-b") / ".gemini" / "account_alias.txt"

    assert marker_a.exists()
    assert marker_b.exists()
    assert marker_a.read_text(encoding="utf-8").strip() == "account-A"
    assert marker_b.read_text(encoding="utf-8").strip() == "account-B"

    # 2. Repeated launches reuse each profile's alias via whoami
    res_a = run_in_profile("profile-a", ["whoami"], capture_output=True)
    assert res_a.returncode == 0
    assert res_a.stdout.strip() == "account-A"

    res_b = run_in_profile("profile-b", ["whoami"], capture_output=True)
    assert res_b.returncode == 0
    assert res_b.stdout.strip() == "account-B"

    # 3. Two concurrent sleeping launches overlap and retain their intended aliases
    ready_a = tmp_path / "ready_a.txt"
    ready_b = tmp_path / "ready_b.txt"

    def _concurrent_worker(p_name: str, expected_alias: str, ready_file: Path):
        res = run_in_profile(
            p_name,
            ["--sleep", "0.4", "whoami"],
            env_overrides={"FAKE_AGY_READY_FILE": str(ready_file)},
            capture_output=True,
        )
        assert res.returncode == 0
        return res.stdout.strip()

    t0 = time.time()
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as executor:
        fut_a = executor.submit(_concurrent_worker, "profile-a", "account-A", ready_a)
        fut_b = executor.submit(_concurrent_worker, "profile-b", "account-B", ready_b)

        both_active = False
        deadline = time.time() + 3.0
        while time.time() < deadline:
            if ready_a.exists() and ready_b.exists():
                both_active = True
                break
            time.sleep(0.01)

        out_a = fut_a.result()
        out_b = fut_b.result()
    dur = time.time() - t0

    assert both_active is True, "Both workers must be simultaneously active"
    assert out_a == "account-A"
    assert out_b == "account-B"
    assert dur < 0.75  # Less than sequential execution (0.4 + 0.4 = 0.8s)

    # 4. Logging out Profile A leaves Profile B intact
    ret_logout = run_in_profile("profile-a", ["--logout"])
    assert ret_logout == 0
    assert not marker_a.exists()
    assert marker_b.exists()

    res_a_after = run_in_profile("profile-a", ["whoami"], capture_output=True)
    assert res_a_after.returncode == 1

    res_b_after = run_in_profile("profile-b", ["whoami"], capture_output=True)
    assert res_b_after.returncode == 0
    assert res_b_after.stdout.strip() == "account-B"

    # 5. Parent environment is unchanged
    assert "MAGY_PROFILE" not in os.environ


def test_cli_profile_create_auth_run(fake_agy, monkeypatch, capfd):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    ret_create = main(["profile", "create", "cli-profile"])
    assert ret_create == 0
    captured = capfd.readouterr()
    assert "Created profile 'cli-profile'" in captured.out
    assert get_profile_home_dir("cli-profile").exists()

    ret_auth = main(["profile", "auth", "cli-profile", "--record-alias", "cli-account"])
    assert ret_auth == 0

    ret_run = main(["profile", "run", "cli-profile", "whoami"])
    assert ret_run == 0
    captured_run = capfd.readouterr()
    assert "cli-account" in captured_run.out


def test_cli_profile_exit_code_forwarding(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_MODE", "auth_error")
    monkeypatch.setenv("FAKE_AGY_EXIT_CODE", "42")

    add_profile("exit-profile", kind="managed")
    ret = main(["profile", "run", "exit-profile", "models"])
    assert ret == 42


def test_cli_profile_preserves_double_dash_args(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    add_profile("dash-profile", kind="managed")
    ret = main(["profile", "run", "dash-profile", "--", "--flag", "--other", "val"])
    assert ret == 0

    invocations = fake_agy.get_invocations()
    assert len(invocations) == 1
    assert invocations[0]["args"][:3] == ["--flag", "--other", "val"]
    assert "--log-file" in invocations[0]["args"]


def test_cli_profile_missing_executable_error(monkeypatch, capsys):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    monkeypatch.setenv("PATH", "")

    add_profile("no-exe-profile", kind="managed")
    ret = main(["profile", "run", "no-exe-profile", "models"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "magy: error:" in captured.err


def test_cli_profile_rejects_unknown_top_level_options():
    with pytest.raises(SystemExit) as exc_info:
        main(["--unknown-opt", "profile", "run", "test-p", "models"])
    assert exc_info.value.code == 2

    with pytest.raises(SystemExit) as exc_info2:
        main(["profile", "--unknown-opt", "run", "test-p", "models"])
    assert exc_info2.value.code == 2

    with pytest.raises(SystemExit) as exc_info3:
        main(["profile", "create", "test-p", "--unexpected"])
    assert exc_info3.value.code == 2


def test_profile_environment_isolation_and_xdg_roots(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    add_profile("xdg-p", kind="managed")
    ret = run_in_profile("xdg-p", ["models"])
    assert ret == 0

    inv = fake_agy.get_invocations()[0]["env"]
    p_home = str(get_profile_home_dir("xdg-p").resolve())
    assert inv["HOME"] == p_home

    if os.name != "nt":
        home_path = Path(p_home)
        assert inv["XDG_CONFIG_HOME"] == str(home_path / ".config")
        assert inv["XDG_DATA_HOME"] == str(home_path / ".local" / "share")
        assert inv["XDG_CACHE_HOME"] == str(home_path / ".cache")
        assert inv["XDG_STATE_HOME"] == str(home_path / ".local" / "state")


def test_profile_launch_skips_indirect_and_prevents_side_effects(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    side_effect = tmp_path / "multicall_side_effect.marker"
    monkeypatch.setenv("FAKE_MULTICALL_SIDE_EFFECT_FILE", str(side_effect))

    # Candidate 1: multicall launcher on PATH
    bin1 = tmp_path / "bin1"
    bin1.mkdir(parents=True, exist_ok=True)
    multicall = bin1 / "fake_manager"
    multicall.write_text(
        f'#!/bin/sh\necho probe > "{side_effect}"\nexit 1\n', encoding="utf-8"
    )
    multicall.chmod(0o755)
    (bin1 / "agy").symlink_to(multicall)

    # Candidate 2: direct fake Agy
    bin2 = tmp_path / "bin2"
    bin2.mkdir(parents=True, exist_ok=True)
    direct = bin2 / "agy"
    direct.write_text(
        f'#!/bin/sh\nexec "{sys.executable}" -m magy.testing.fake_agy "$@"\n',
        encoding="utf-8",
    )
    direct.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin1}:{bin2}")

    add_profile("skip-indirect-p", kind="managed")
    ret = run_in_profile("skip-indirect-p", ["models"])
    assert ret == 0
    assert not side_effect.exists()

    # Profile home layout must not contain any tool-manager dirs
    p_home = get_profile_home_dir("skip-indirect-p")
    assert not (p_home / ".local" / "share" / "mise").exists()


def test_profile_explicit_xdg_env_preserved(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    add_profile("custom-xdg", kind="managed")
    ret = run_in_profile(
        "custom-xdg",
        ["models"],
        env_overrides={"XDG_CONFIG_HOME": "/custom/config/path"},
    )
    assert ret == 0

    inv = fake_agy.get_invocations()[0]["env"]
    assert inv["XDG_CONFIG_HOME"] == "/custom/config/path"


def test_remove_profile_blocks_while_shared_lease_held():
    """H2: Exclusive removal is blocked while any shared lease is active."""
    from magy.profiles import (
        acquire_profile_lease,
        get_active_operation_count,
        remove_profile,
    )

    add_profile("lease-block-p", kind="managed")
    assert get_active_operation_count("lease-block-p") == 0

    with acquire_profile_lease("lease-block-p", exclusive=False):
        assert get_active_operation_count("lease-block-p") == 1
        with pytest.raises(RuntimeError, match="active operations are running"):
            remove_profile("lease-block-p")

    assert get_active_operation_count("lease-block-p") == 0
    remove_profile("lease-block-p")
    assert get_profile("lease-block-p") is None


def test_remove_profile_incarnation_aba_prevention(monkeypatch):
    """H3: Verify incarnation_id prevents deleting replacement profile."""
    import magy.profiles
    from magy.profiles import get_registry_file_path, remove_profile, update_json

    add_profile("aba-p", kind="managed")
    orig = get_profile("aba-p")
    assert orig is not None

    # Simulate ABA: remove targets orig, but registry was modified concurrently
    path = get_registry_file_path()

    def _swap_incarnation(data):
        data["profiles"]["aba-p"]["incarnation_id"] = "new-replacement-incarnation"
        return data

    update_json(path, _swap_incarnation)
    monkeypatch.setattr(magy.profiles, "get_profile", lambda name: orig)

    with pytest.raises(ValueError, match="incarnation mismatch"):
        remove_profile("aba-p")


def test_remove_profile_rollback_on_registry_failure(monkeypatch):
    """H3: If registry update fails, staged directory rename is rolled back."""
    import magy.profiles
    from magy.profiles import get_profile_dir, remove_profile

    add_profile("rollback-p", kind="managed")
    p_dir = get_profile_dir("rollback-p")
    assert p_dir.exists()

    def _failing_update(path, update_fn, **kwargs):
        raise OSError("Simulated disk error during registry update")

    monkeypatch.setattr(magy.profiles, "update_json", _failing_update)

    with pytest.raises(OSError, match="Simulated disk error"):
        remove_profile("rollback-p")

    # Profile directory must be restored / rolled back
    assert p_dir.exists()


@pytest.mark.skipif(os.name == "nt", reason="Process group and signals for POSIX")
def test_run_in_profile_timeout_kills_process_group(tmp_path, monkeypatch):
    """H4: Timeout cleanly terminates child process tree and records health."""
    import subprocess
    import sys

    from magy.profiles import get_active_operation_count, run_in_profile

    script = tmp_path / "hang_with_child.py"
    script.write_text(
        "import subprocess, time, sys\n"
        "cmd = [sys.executable, '-c', 'import time; time.sleep(30)']\n"
        "proc = subprocess.Popen(cmd)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    add_profile("timeout-tree-p", kind="managed")

    with pytest.raises(subprocess.TimeoutExpired):
        run_in_profile(
            "timeout-tree-p",
            [str(script)],
            executable=Path(sys.executable),
            timeout=0.3,
            update_health=True,
        )

    # Activity count must be 0 after timeout cleanup
    assert get_active_operation_count("timeout-tree-p") == 0

    # Profile health must be classified as timeout
    p = get_profile("timeout-tree-p")
    assert p.health == "timeout"
    assert p.cooldown_until is not None


@pytest.mark.skipif(os.name == "nt", reason="Signal exit codes for POSIX")
def test_run_in_profile_signal_exit_code_normalized(tmp_path):
    """M1: Return normalized 128 + signum for signal-terminated child."""
    import sys

    from magy.profiles import run_in_profile

    script = tmp_path / "sig_exit.py"
    script.write_text(
        "import os, signal\nos.kill(os.getpid(), signal.SIGTERM)\n",
        encoding="utf-8",
    )

    add_profile("signal-exit-p", kind="managed")
    ret = run_in_profile(
        "signal-exit-p",
        [str(script)],
        executable=Path(sys.executable),
    )
    # Signal 15 -> 128 + 15 = 143
    assert ret == 143


@pytest.mark.skipif(os.name == "nt", reason="Process group and signals for POSIX")
@pytest.mark.parametrize("capture_output", [False, True])
def test_run_in_profile_timeout_kills_descendant_ignoring_sigterm(
    tmp_path, capture_output
):
    """H2: Timeout escalates to SIGKILL and kills descendant ignoring SIGTERM.

    Verifies process group cleanup completes even if the direct child exits.
    """
    import subprocess
    import sys

    from magy.profiles import get_active_operation_count, run_in_profile

    pid_file = tmp_path / "descendant.pid"

    descendant_script = tmp_path / "descendant.py"
    descendant_script.write_text(
        "import os, signal, time, pathlib\n"
        "if os.fork() > 0: os._exit(0)\n"
        "os.setsid()\n"
        "if os.fork() > 0: os._exit(0)\n"
        "signal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
        f"pathlib.Path(r'{pid_file}').write_text(str(os.getpid()))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    direct_script = tmp_path / "direct_child.py"
    direct_script.write_text(
        "import subprocess, sys, signal, time\n"
        f"cmd = [sys.executable, r'{descendant_script}']\n"
        "proc = subprocess.Popen(cmd)\n"
        "signal.signal(signal.SIGTERM, lambda s, f: sys.exit(0))\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )

    add_profile("timeout-descendant-p", kind="managed")

    with pytest.raises(subprocess.TimeoutExpired):
        run_in_profile(
            "timeout-descendant-p",
            [str(direct_script)],
            executable=Path(sys.executable),
            capture_output=capture_output,
            timeout=0.3,
            update_health=True,
        )

    # 1. Descendant PID was recorded
    assert pid_file.exists()
    descendant_pid = int(pid_file.read_text().strip())

    # 2. Descendant must be dead (killed by SIGKILL escalation)
    with pytest.raises(ProcessLookupError):
        os.kill(descendant_pid, 0)

    # 3. Lease count must be 0 after cleanup
    assert get_active_operation_count("timeout-descendant-p") == 0


def test_remove_profile_legacy_registry_migration_and_removal():
    """M1: Legacy registries without incarnation_id are migrated cleanly."""
    from magy.config import get_data_dir
    from magy.profiles import (
        ensure_profile_layout,
        get_profile,
        load_profiles,
        remove_profile,
    )
    from magy.storage import atomic_write_json

    reg_path = get_data_dir() / "profiles.json"
    legacy_data = {
        "version": 1,
        "profiles": {
            "legacy-p": {
                "name": "legacy-p",
                "kind": "managed",
                "home_dir": str(get_profile_home_dir("legacy-p")),
                "enabled": True,
                "created_at": 1700000000.0,
                "health": "healthy",
                "cooldown_reason": "Incorrect API key provided: sk-proj-fake-secret",
            }
        },
    }
    ensure_profile_layout("legacy-p")
    atomic_write_json(reg_path, legacy_data, lock=True)

    # load_profiles migrates and persists incarnation_id
    profs = load_profiles()
    assert "legacy-p" in profs
    assert profs["legacy-p"].incarnation_id is not None
    assert (
        profs["legacy-p"].cooldown_reason
        == "Failure details removed during security migration"
    )

    # remove_profile succeeds without incarnation mismatch
    remove_profile("legacy-p")
    assert get_profile("legacy-p") is None


def test_run_in_profile_unregistered_name_rejected():
    """M2: Reject unregistered profile name without creating home or layout."""
    with pytest.raises(KeyError, match="not registered"):
        run_in_profile("never-registered-p", ["models"])

    home_dir = get_profile_home_dir("never-registered-p")
    assert not home_dir.exists()


def test_lifecycle_lease_locking_helpers(monkeypatch):
    """H5: Verify lease locking semantics and unsupported platform rejection."""
    import magy.profiles

    monkeypatch.setattr(magy.profiles, "fcntl", None)
    monkeypatch.setattr(os, "name", "unknown_os")

    with pytest.raises(NotImplementedError, match="not supported"):
        magy.profiles._lock_fd(123, exclusive=True)


def test_remove_profile_retries_staged_storage_deletion(monkeypatch):
    import magy.profiles

    add_profile("cleanup-rollback-p", kind="managed")
    profile_dir = get_profile_dir("cleanup-rollback-p")
    original_rmtree = shutil.rmtree

    def _fail_staged_delete(path, *args, **kwargs):
        if Path(path).name.startswith(".deleting_cleanup-rollback-p_"):
            raise OSError("simulated cleanup failure")
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(magy.profiles.shutil, "rmtree", _fail_staged_delete)

    with pytest.raises(OSError, match="run profile remove again"):
        magy.profiles.remove_profile("cleanup-rollback-p")

    assert get_profile("cleanup-rollback-p") is None
    assert not profile_dir.exists()
    assert list(profile_dir.parent.glob(".deleting_cleanup-rollback-p_*"))

    with pytest.raises(RuntimeError, match="pending removal cleanup"):
        add_profile("cleanup-rollback-p", kind="managed")

    monkeypatch.setattr(magy.profiles.shutil, "rmtree", original_rmtree)
    magy.profiles.remove_profile("cleanup-rollback-p")
    assert not list(profile_dir.parent.glob(".deleting_cleanup-rollback-p_*"))


def test_remove_profile_cleans_private_run_logs():
    from magy.config import get_state_dir
    from magy.profiles import remove_profile

    add_profile("log-cleanup-p", kind="managed")
    logs_dir = get_state_dir() / "logs" / "log-cleanup-p"
    logs_dir.mkdir(parents=True)
    (logs_dir / "run.log").write_text("private diagnostic", encoding="utf-8")

    remove_profile("log-cleanup-p")

    assert not logs_dir.exists()


@pytest.mark.skipif(os.name == "nt", reason="Process group and signals for POSIX")
def test_forwarded_sigterm_escalates_for_ignoring_child(tmp_path):
    import subprocess

    runner = (
        "import signal, sys\n"
        "from pathlib import Path\n"
        "from magy.profiles import add_profile, run_in_profile\n"
        "add_profile('signal-ignore-p')\n"
        'child = ("import signal, time; "\n'
        '         "signal.signal(signal.SIGTERM, signal.SIG_IGN); "\n'
        '         "time.sleep(30)")\n'
        "raise SystemExit(run_in_profile(\n"
        "    'signal-ignore-p', ['-c', child], executable=Path(sys.executable)\n"
        "))\n"
    )
    proc = subprocess.Popen(
        [sys.executable, "-c", runner],
        env=os.environ,
        process_group=0,
    )
    time.sleep(0.5)
    os.kill(proc.pid, signal.SIGTERM)

    assert proc.wait(timeout=5.0) == 137


@pytest.mark.skipif(os.name == "nt", reason="POSIX job control")
def test_stopped_interactive_child_does_not_steal_terminal_after_bg(monkeypatch):
    import magy.profiles

    class FakeProcess:
        pid = 4321
        args = ["fake"]
        returncode = None

        def poll(self):
            return self.returncode

    stopped_status = (signal.SIGTSTP << 8) | 0x7F
    statuses = iter([(4321, stopped_status), (4321, 0)])
    foreground_groups = iter([4321, 9999])
    foreground_changes = []
    signals = []

    monkeypatch.setattr(os, "waitpid", lambda *args: next(statuses))
    monkeypatch.setattr(os, "tcgetpgrp", lambda fd: next(foreground_groups))
    monkeypatch.setattr(os, "kill", lambda pid, sig: signals.append((pid, sig)))
    monkeypatch.setattr(os, "killpg", lambda pgid, sig: signals.append((pgid, sig)))
    monkeypatch.setattr(
        magy.profiles,
        "_set_terminal_foreground_pgrp",
        lambda fd, pgid: foreground_changes.append((fd, pgid)),
    )

    result = magy.profiles._wait_interactive_child(
        FakeProcess(), timeout=1.0, tty_fd=9, parent_pgrp=1234
    )

    assert result == 0
    assert foreground_changes == [(9, 1234)]
    assert (4321, signal.SIGCONT) in signals
