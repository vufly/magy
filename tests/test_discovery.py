from pathlib import Path

from magy.agy import get_agy_version, resolve_agy_executable


def test_discovery_empty(monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    exe, source = resolve_agy_executable(path_env="")
    assert exe is None
    assert source is None


def test_discovery_precedence_env(fake_agy, monkeypatch, tmp_path: Path):
    fake_exe = fake_agy.executable
    monkeypatch.setenv("MAGY_AGY_CMD", str(fake_exe))

    # Also set config and PATH to dummy paths
    dummy_config = tmp_path / "dummy_agy"
    dummy_config.write_text("#!/bin/sh\n", encoding="utf-8")
    dummy_config.chmod(0o755)

    exe, source = resolve_agy_executable(configured_cmd=str(dummy_config))
    assert exe == fake_exe.resolve()
    assert source == "MAGY_AGY_CMD"


def test_discovery_precedence_config(fake_agy, monkeypatch):
    fake_exe = fake_agy.executable
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    exe, source = resolve_agy_executable(configured_cmd=str(fake_exe))
    assert exe == fake_exe.resolve()
    assert source == "config"


def test_discovery_precedence_path(fake_agy, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    fake_bin_dir = str(fake_agy.executable.parent)

    exe, source = resolve_agy_executable(path_env=fake_bin_dir)
    assert exe == fake_agy.executable.resolve()
    assert source == "PATH"


def test_discovery_invalid_env_cmd(monkeypatch):
    monkeypatch.setenv("MAGY_AGY_CMD", "/nonexistent/path/to/agy")
    exe, source = resolve_agy_executable()
    assert exe is None
    assert source is not None
    assert "MAGY_AGY_CMD" in source
    assert "not found" in source


def test_discovery_invalid_config_cmd(monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    exe, source = resolve_agy_executable(configured_cmd="/nonexistent/path/to/agy")
    assert exe is None
    assert source is not None
    assert "config" in source
    assert "not found" in source


def test_get_agy_version_success(fake_agy, monkeypatch):
    monkeypatch.setenv("FAKE_AGY_VERSION", "2.0.0-test")
    ver = get_agy_version(fake_agy.executable)
    assert ver == "2.0.0-test"


def test_get_agy_version_failure(tmp_path: Path):
    non_executable = tmp_path / "not_an_exe.txt"
    non_executable.write_text("hello", encoding="utf-8")
    ver = get_agy_version(non_executable)
    assert ver is None
