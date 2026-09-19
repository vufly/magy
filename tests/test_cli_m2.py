import json

import pytest

from magy.cli import main
from magy.profiles import get_profile, update_profile_health


def test_cli_profile_add_and_list(capfd):
    ret_add = main(["profile", "add", "test-add"])
    assert ret_add == 0
    captured = capfd.readouterr()
    assert "Added managed profile 'test-add'" in captured.out

    ret_list = main(["profile", "list", "--json"])
    assert ret_list == 0
    data = json.loads(capfd.readouterr().out)
    assert any(p["name"] == "test-add" for p in data)


def test_cli_profile_add_current(capfd):
    ret = main(["profile", "add", "my-current", "--current"])
    assert ret == 0
    captured = capfd.readouterr()
    assert "Added external profile 'my-current'" in captured.out
    p = get_profile("my-current")
    assert p is not None
    assert p.kind == "external"


def test_cli_profile_show(capfd):
    main(["profile", "add", "show-p"])
    capfd.readouterr()

    ret = main(["profile", "show", "show-p", "--json"])
    assert ret == 0
    data = json.loads(capfd.readouterr().out)
    assert data["name"] == "show-p"
    assert data["kind"] == "managed"
    assert data["health"] == "untested"


def test_cli_profile_enable_disable(capfd):
    main(["profile", "add", "dis-p"])
    capfd.readouterr()

    ret_dis = main(["profile", "disable", "dis-p"])
    assert ret_dis == 0
    p_dis = get_profile("dis-p")
    assert p_dis.enabled is False

    ret_en = main(["profile", "enable", "dis-p"])
    assert ret_en == 0
    p_en = get_profile("dis-p")
    assert p_en.enabled is True


def test_cli_profile_reset_health(capfd):
    main(["profile", "add", "res-p"])
    update_profile_health("res-p", health="rate-limited", cooldown_seconds=60.0)

    p_bad = get_profile("res-p")
    assert p_bad.health == "rate-limited"

    ret = main(["profile", "reset-health", "res-p"])
    assert ret == 0
    p_good = get_profile("res-p")
    assert p_good.health == "healthy"
    assert p_good.cooldown_until is None


def test_cli_profile_remove(capfd):
    main(["profile", "add", "rm-p"])
    capfd.readouterr()

    ret = main(["profile", "remove", "rm-p", "--force"])
    assert ret == 0
    assert get_profile("rm-p") is None


def test_cli_profile_remove_noninteractive_requires_force(monkeypatch, capsys):
    main(["profile", "add", "rm-noforce"])
    capsys.readouterr()

    # In test environment, stdin is not a tty
    ret = main(["profile", "remove", "rm-noforce"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "requires --force in noninteractive mode" in captured.err


def test_cli_status(capfd):
    main(["profile", "add", "stat-1"])
    capfd.readouterr()

    ret = main(["status", "--json"])
    assert ret == 0
    data = json.loads(capfd.readouterr().out)
    assert data["total_profiles"] == 1
    assert data["enabled_profiles"] == 1


def test_cli_passthrough_automatic_round_robin(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    main(["profile", "add", "auto-a"])
    main(["profile", "add", "auto-b"])
    capsys.readouterr()

    # Launch 1: should choose auto-a
    ret1 = main(["--", "models"])
    assert ret1 == 0
    captured1 = capsys.readouterr()
    assert "[magy] using profile: auto-a" in captured1.err

    # Launch 2: should choose auto-b
    ret2 = main(["--", "models"])
    assert ret2 == 0
    captured2 = capsys.readouterr()
    assert "[magy] using profile: auto-b" in captured2.err


def test_cli_passthrough_explicit_profile(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    main(["profile", "add", "work"])
    main(["profile", "add", "play"])
    capsys.readouterr()

    ret_rec = main(["--profile", "play", "--", "--record-alias", "play-user"])
    assert ret_rec == 0

    ret = main(["--profile", "play", "--", "whoami"])
    assert ret == 0
    captured = capsys.readouterr()
    assert "[magy] using profile: play" in captured.err


def test_cli_passthrough_rejects_unknown_option_before_double_dash():
    with pytest.raises(SystemExit) as exc_info:
        main(["--bogus-opt", "--", "models"])
    assert exc_info.value.code == 2


def test_cli_version_flag(capfd):
    ret1 = main(["--version"])
    assert ret1 == 0
    assert "magy 0.1.0" in capfd.readouterr().out

    ret2 = main(["-V"])
    assert ret2 == 0
    assert "magy 0.1.0" in capfd.readouterr().out


def test_cli_missing_profile_arg_rejected():
    with pytest.raises(SystemExit) as exc_info1:
        main(["--profile"])
    assert exc_info1.value.code == 2

    with pytest.raises(SystemExit) as exc_info2:
        main(["--profile", "--", "models"])
    assert exc_info2.value.code == 2

    with pytest.raises(SystemExit) as exc_info3:
        main(["--profile="])
    assert exc_info3.value.code == 2


def test_cli_profile_equals_form(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    main(["profile", "add", "equals-p"])
    capsys.readouterr()

    ret = main(["--profile=equals-p", "--", "models"])
    assert ret == 0
    assert "[magy] using profile: equals-p" in capsys.readouterr().err


def test_cli_misplaced_subcommand_rejected():
    main(["profile", "add", "misplaced-p"])

    with pytest.raises(SystemExit) as exc_info:
        main(["--profile", "misplaced-p", "status"])
    assert exc_info.value.code == 2


def test_cli_auth_and_run_unregistered_rejected(capsys):
    """M2: Reject auth and run on unregistered profiles without creating state."""
    from magy.profiles import get_profile_home_dir

    ret_auth = main(["profile", "auth", "nonexistent-auth-p"])
    assert ret_auth == 1
    err_auth = capsys.readouterr().err
    assert "not registered" in err_auth
    assert not get_profile_home_dir("nonexistent-auth-p").exists()

    ret_run = main(["profile", "run", "nonexistent-run-p", "models"])
    assert ret_run == 1
    err_run = capsys.readouterr().err
    assert "not registered" in err_run
    assert not get_profile_home_dir("nonexistent-run-p").exists()
