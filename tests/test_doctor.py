import json

import pytest

from magy.cli import main


def test_doctor_healthy(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_VERSION", "1.2.6")

    ret = main(["doctor"])
    assert ret == 0

    captured = capsys.readouterr()
    assert "Magy Diagnostics" in captured.out
    assert "Status: OK - all prerequisites satisfied." in captured.out
    assert "1.2.6" in captured.out
    assert str(fake_agy.executable.resolve()) in captured.out

    # Ensure no credential or token terms appear
    assert "token" not in captured.out.lower()
    assert "credential" not in captured.out.lower()
    assert "secret" not in captured.out.lower()


def test_doctor_missing_executable(monkeypatch, capsys):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    ret = main(["doctor"])
    assert ret == 1

    captured = capsys.readouterr()
    assert "Status: FAILED - missing prerequisites:" in captured.out
    assert "Agy executable not found" in captured.out


def test_doctor_json_healthy(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_VERSION", "1.2.6")

    ret = main(["doctor", "--json"])
    assert ret == 0

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["healthy"] is True
    assert data["agy"]["version"] == "1.2.6"
    assert data["agy"]["executable"] == str(fake_agy.executable.resolve())
    assert len(data["missing_prerequisites"]) == 0


def test_doctor_json_unhealthy(monkeypatch, capsys):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    ret = main(["doctor", "--json"])
    assert ret == 1

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["healthy"] is False
    assert len(data["missing_prerequisites"]) > 0


def test_doctor_malformed_config_json(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{\ninvalid json", encoding="utf-8")

    ret = main(["doctor"])
    assert ret == 1

    captured = capsys.readouterr()
    assert "Config Error:" in captured.out
    assert "Malformed JSON" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1

    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["healthy"] is False
    assert "Malformed JSON" in data["config_error"]


def test_doctor_invalid_agy_cmd_type(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('{"agy_cmd": 12345}', encoding="utf-8")

    ret = main(["doctor"])
    assert ret == 1

    captured = capsys.readouterr()
    assert "Config Error:" in captured.out
    assert "agy_cmd must be a string or null" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is False
    assert "agy_cmd must be a string or null" in data["config_error"]


def test_doctor_empty_agy_cmd(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('{"agy_cmd": "   "}', encoding="utf-8")

    ret = main(["doctor"])
    assert ret == 1

    captured = capsys.readouterr()
    assert "Config Error:" in captured.out
    assert "empty string" in captured.out


def test_doctor_config_lock_timeout(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    from filelock import FileLock

    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text("{}", encoding="utf-8")

    lock_path = cfg_path.with_name(f".{cfg_path.name}.lock")
    lock = FileLock(str(lock_path))

    with lock:
        ret = main(["doctor"])
        assert ret == 1

        captured = capsys.readouterr()
        assert "Config Error:" in captured.out
        assert "Timed out acquiring lock" in captured.out


def test_doctor_config_path_resolution_error(fake_agy, monkeypatch, capsys):
    """Regression test for R1: get_config_file_path raises PermissionError."""
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    import magy.config

    def _denied(*args, **kwargs):
        raise PermissionError("denied access to config path")

    monkeypatch.setattr(magy.config, "get_config_file_path", _denied)

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Config Error:" in captured.out
    assert "denied access to config path" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is False
    assert any(
        "denied access to config path" in m for m in data["missing_prerequisites"]
    )


def test_doctor_config_root_error(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    import magy.agy

    def _denied_config(*args, **kwargs):
        raise PermissionError("Access denied on config dir")

    monkeypatch.setattr(magy.agy, "get_config_dir", _denied_config)

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Permission denied accessing config directory" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is False
    assert any("config directory" in m for m in data["missing_prerequisites"])


def test_doctor_data_root_error(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    import magy.agy

    def _denied_data(*args, **kwargs):
        raise PermissionError("Access denied on data dir")

    monkeypatch.setattr(magy.agy, "get_data_dir", _denied_data)

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Permission denied accessing data directory" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is False
    assert any("data directory" in m for m in data["missing_prerequisites"])


def test_doctor_state_root_error(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    import magy.agy

    def _os_error_state(*args, **kwargs):
        raise OSError("Corrupt state filesystem")

    monkeypatch.setattr(magy.agy, "get_state_dir", _os_error_state)

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Error accessing state directory" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is False
    assert any("state directory" in m for m in data["missing_prerequisites"])


@pytest.mark.parametrize(
    "env_var",
    ["MAGY_CONFIG_DIR", "MAGY_DATA_DIR", "MAGY_STATE_DIR", "MAGY_AGY_CMD"],
)
def test_doctor_unknown_user_env_vars(env_var, fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv(env_var, "~__magy_missing_user__/path")

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Status: FAILED - missing prerequisites:" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    captured_json = capsys.readouterr()
    assert "Traceback" not in captured_json.err
    data = json.loads(captured_json.out)
    assert data["healthy"] is False
    assert len(data["missing_prerequisites"]) > 0


def test_doctor_unknown_user_configured_agy_cmd(fake_agy, monkeypatch, capsys):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    cfg_path.write_text('{"agy_cmd": "~__magy_missing_user__/agy"}', encoding="utf-8")

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Status: FAILED - missing prerequisites:" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    captured_json = capsys.readouterr()
    data = json.loads(captured_json.out)
    assert data["healthy"] is False
    assert any("config" in m for m in data["missing_prerequisites"])


def test_doctor_root_permission_enforcement_failure(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))

    def _fail_private_dir(path):
        raise PermissionError(f"Failed to enforce private permissions on {path}")

    monkeypatch.setattr("magy.config.ensure_private_directory", _fail_private_dir)

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Traceback" not in captured.err
    assert "Permission denied accessing" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 1
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is False
    assert any(
        "Permission denied accessing" in m for m in data["missing_prerequisites"]
    )
