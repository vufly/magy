import json

import pytest

from magy.agy import evaluate_agy_compatibility
from magy.cli import main


def test_evaluate_agy_compatibility():
    assert "verified" in evaluate_agy_compatibility("1.2.6")
    assert "verified" in evaluate_agy_compatibility("v1.2.6")
    assert "verified" in evaluate_agy_compatibility("1.1.0")
    assert "verified" in evaluate_agy_compatibility("1.0.4")
    assert "unverified" in evaluate_agy_compatibility("2.0.0")
    assert "unverified" in evaluate_agy_compatibility("unknown")
    assert "unverified" in evaluate_agy_compatibility(None)


def test_doctor_healthy(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_VERSION", "1.2.6")

    ret = main(["doctor"])
    assert ret == 0

    captured = capsys.readouterr()
    assert "Magy Diagnostics" in captured.out
    assert "Status: OK - all prerequisites satisfied." in captured.out
    assert "1.2.6" in captured.out
    assert (
        "Compatibility: verified (1.2.6 tested with profile isolation)" in captured.out
    )
    assert str(fake_agy.executable.resolve()) in captured.out

    # Ensure no credential or token terms appear
    assert "token" not in captured.out.lower()
    assert "credential" not in captured.out.lower()
    assert "secret" not in captured.out.lower()


def test_doctor_unverified_version(fake_agy, monkeypatch, capsys):
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_agy.executable))
    monkeypatch.setenv("FAKE_AGY_VERSION", "2.1.0")

    ret = main(["doctor"])
    assert ret == 0

    captured = capsys.readouterr()
    assert "Magy Diagnostics" in captured.out
    assert "Status: OK - all prerequisites satisfied." in captured.out
    assert "2.1.0" in captured.out
    assert "Compatibility: unverified (2.1.0 not verified" in captured.out

    ret_json = main(["doctor", "--json"])
    assert ret_json == 0
    data = json.loads(capsys.readouterr().out)
    assert data["healthy"] is True
    assert "unverified (2.1.0 not verified" in data["agy"]["compatibility"]


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
    assert (
        data["agy"]["compatibility"] == "verified (1.2.6 tested with profile isolation)"
    )
    assert data["agy"]["executable"] == str(fake_agy.executable.resolve())
    assert len(data["missing_prerequisites"]) == 0


def test_doctor_json_unhealthy(monkeypatch, capsys):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    ret = main(["doctor", "--json"])
    assert ret == 1

    captured = capsys.readouterr()
    data = json.loads(captured.out)
    assert data["healthy"] is False
    assert data["agy"]["compatibility"] is None
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


def test_doctor_reports_direct_path_and_never_probes_indirect(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    side_effect = tmp_path / "multicall_probed.marker"
    monkeypatch.setenv("FAKE_MULTICALL_SIDE_EFFECT_FILE", str(side_effect))

    # Candidate 1: indirect shim
    bin1 = tmp_path / "bin1"
    bin1.mkdir(parents=True, exist_ok=True)
    multicall = bin1 / "fake_manager"
    multicall.write_text(
        f'#!/bin/sh\necho probe > "{side_effect}"\nexit 1\n', encoding="utf-8"
    )
    multicall.chmod(0o755)
    (bin1 / "agy").symlink_to(multicall)

    # Candidate 2: direct executable
    bin2 = tmp_path / "bin2"
    bin2.mkdir(parents=True, exist_ok=True)
    direct = bin2 / "agy"
    direct.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
        '  echo "1.2.6-direct"\n  exit 0\nfi\nexit 0\n',
        encoding="utf-8",
    )
    direct.chmod(0o755)

    monkeypatch.setenv("PATH", f"{bin1}:{bin2}")

    ret = main(["doctor"])
    assert ret == 0
    captured = capsys.readouterr()
    assert str(direct) in captured.out
    assert "1.2.6-direct" in captured.out
    assert not side_effect.exists()


def test_doctor_indirect_only_fails_without_invoking_launcher(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    side_effect = tmp_path / "multicall_probed.marker"
    monkeypatch.setenv("FAKE_MULTICALL_SIDE_EFFECT_FILE", str(side_effect))

    bin1 = tmp_path / "bin1"
    bin1.mkdir(parents=True, exist_ok=True)
    multicall = bin1 / "mise"
    multicall.write_text(
        f'#!/bin/sh\necho probe > "{side_effect}"\nexit 1\n', encoding="utf-8"
    )
    multicall.chmod(0o755)
    (bin1 / "agy").symlink_to(multicall)

    monkeypatch.setenv("PATH", str(bin1))

    ret = main(["doctor"])
    assert ret == 1
    captured = capsys.readouterr()
    assert "Status: FAILED" in captured.out
    assert "indirect launcher" in captured.out
    assert not side_effect.exists()


def test_doctor_resolver_dynamic_upgrade_reporting(tmp_path, monkeypatch, capsys):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    v1_exe = tmp_path / "v1_agy"
    v1_exe.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
        '  echo "1.0.0-v1"\n  exit 0\nfi\nexit 0\n',
        encoding="utf-8",
    )
    v1_exe.chmod(0o755)

    v2_exe = tmp_path / "v2_agy"
    v2_exe.write_text(
        '#!/bin/sh\nif [ "$1" = "--version" ]; then\n'
        '  echo "2.0.0-v2"\n  exit 0\nfi\nexit 0\n',
        encoding="utf-8",
    )
    v2_exe.chmod(0o755)

    state_file = tmp_path / "active_manager_state.txt"
    state_file.write_text(str(v1_exe), encoding="utf-8")

    resolver_script = tmp_path / "resolver.py"
    resolver_code = (
        "import sys\n"
        "from pathlib import Path\n"
        f'print(Path("{state_file}").read_text(encoding="utf-8").strip())\n'
    )
    resolver_script.write_text(resolver_code, encoding="utf-8")

    from magy.config import get_config_file_path

    cfg_path = get_config_file_path()
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    import json
    import sys

    cfg_path.write_text(
        json.dumps({"agy_resolver": [sys.executable, str(resolver_script)]}),
        encoding="utf-8",
    )

    # First doctor check
    ret1 = main(["doctor"])
    assert ret1 == 0
    out1 = capsys.readouterr().out
    assert str(v1_exe) in out1
    assert "1.0.0-v1" in out1

    # Simulate upgrade
    state_file.write_text(str(v2_exe), encoding="utf-8")

    # Second doctor check
    ret2 = main(["doctor"])
    assert ret2 == 0
    out2 = capsys.readouterr().out
    assert str(v2_exe) in out2
    assert "2.0.0-v2" in out2
