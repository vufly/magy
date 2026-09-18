import concurrent.futures
import os
import shutil
import stat
import time
from pathlib import Path

import pytest

from magy.cli import main
from magy.profiles import (
    PROTECTED_ENV_VARS,
    build_profile_env,
    ensure_profile_layout,
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
    p_dir, p_home = ensure_profile_layout("symlink-cred")
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

    p_dir, p_home = ensure_profile_layout("real-home-symlink")
    gemini_dir = p_home / ".gemini"
    shutil.rmtree(gemini_dir)
    gemini_dir.symlink_to(real_gemini)

    with pytest.raises(ValueError, match="cannot be a symlink"):
        validate_profile_layout("real-home-symlink")

    with pytest.raises(ValueError, match="cannot be a symlink"):
        run_in_profile("real-home-symlink", ["models"])


@pytest.mark.skipif(os.name == "nt", reason="Symlink tests for POSIX")
def test_profile_layout_rejects_cross_profile_symlink():
    ensure_profile_layout("profile-alpha")
    _p_dir_beta, p_home_beta = ensure_profile_layout("profile-beta")

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
    cfg_path.write_text(
        f'{{"agy_cmd": "{fake_agy.executable}"}}', encoding="utf-8"
    )

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

    with pytest.raises(FileNotFoundError, match="Agy executable invalid"):
        run_in_profile("bad-cfg", ["models"])


def test_stage2_persistent_fake_accounts(fake_agy, monkeypatch, tmp_path: Path):
    """Stage 2 verification: persistent account alias markers in profile homes."""
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

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

    ret = main(["profile", "run", "exit-profile", "models"])
    assert ret == 42


def test_cli_profile_preserves_double_dash_args(fake_agy, monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    ret = main(["profile", "run", "dash-profile", "--", "--flag", "--other", "val"])
    assert ret == 0

    invocations = fake_agy.get_invocations()
    assert len(invocations) == 1
    assert invocations[0]["args"] == ["--flag", "--other", "val"]


def test_cli_profile_missing_executable_error(monkeypatch, capsys):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    monkeypatch.setenv("PATH", "")

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
