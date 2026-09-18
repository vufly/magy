import os
import stat
import sys
from pathlib import Path

from magy.agy import get_agy_version, resolve_agy_executable


def _make_executable_script(path: Path, content: str = "") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not content:
        content = f'#!/bin/sh\nexec "{sys.executable}" -m magy.testing.fake_agy "$@"\n'
    path.write_text(content, encoding="utf-8")
    mode = path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    path.chmod(mode)
    return path


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
    _make_executable_script(dummy_config)

    exe, source = resolve_agy_executable(configured_cmd=str(dummy_config))
    assert exe == fake_exe
    assert source == "MAGY_AGY_CMD"


def test_discovery_precedence_config(fake_agy, monkeypatch):
    fake_exe = fake_agy.executable
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    exe, source = resolve_agy_executable(configured_cmd=str(fake_exe))
    assert exe == fake_exe
    assert source == "config"


def test_discovery_precedence_path(fake_agy, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    fake_bin_dir = str(fake_agy.executable.parent)

    exe, source = resolve_agy_executable(path_env=fake_bin_dir)
    assert exe == fake_agy.executable
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


def test_discovery_path_skips_indirect_and_selects_direct(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    side_effect = tmp_path / "forbidden_multicall.marker"
    monkeypatch.setenv("FAKE_MULTICALL_SIDE_EFFECT_FILE", str(side_effect))

    # Candidate 1: multicall launcher
    bin1 = tmp_path / "bin1"
    multicall_bin = _make_executable_script(
        bin1 / "fake_manager",
        f'#!/bin/sh\necho forbidden > "{side_effect}"\nexit 1\n',
    )
    shim_link = bin1 / "agy"
    shim_link.symlink_to(multicall_bin)

    # Candidate 2: direct agy executable
    bin2 = tmp_path / "bin2"
    direct_agy = _make_executable_script(bin2 / "agy")

    path_env = f"{bin1}{os.pathsep}{bin2}"
    exe, source = resolve_agy_executable(path_env=path_env)

    assert exe == direct_agy
    assert source == "PATH"
    assert not side_effect.exists()


def test_discovery_indirect_only_path_fails_without_side_effects(
    tmp_path: Path, monkeypatch
):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    side_effect = tmp_path / "forbidden_multicall.marker"
    monkeypatch.setenv("FAKE_MULTICALL_SIDE_EFFECT_FILE", str(side_effect))

    bin1 = tmp_path / "bin1"
    multicall_bin = _make_executable_script(
        bin1 / "mise",
        f'#!/bin/sh\necho forbidden > "{side_effect}"\nexit 1\n',
    )
    shim_link = bin1 / "agy"
    shim_link.symlink_to(multicall_bin)

    exe, source = resolve_agy_executable(path_env=str(bin1))
    assert exe is None
    assert source is not None
    assert "PATH" in source
    assert "indirect launcher" in source
    assert not side_effect.exists()


def test_discovery_indirect_explicit_env_and_config_fail_without_fallback(
    tmp_path: Path, monkeypatch
):
    # Candidate 1: indirect symlink
    bin1 = tmp_path / "bin1"
    multicall_bin = _make_executable_script(bin1 / "launcher")
    shim_link = bin1 / "agy"
    shim_link.symlink_to(multicall_bin)

    # Candidate 2: direct executable on PATH
    bin2 = tmp_path / "bin2"
    _make_executable_script(bin2 / "agy")

    # Explicit env pointing to indirect candidate must fail without falling back to PATH
    monkeypatch.setenv("MAGY_AGY_CMD", str(shim_link))
    exe, source = resolve_agy_executable(path_env=str(bin2))
    assert exe is None
    assert "MAGY_AGY_CMD" in source
    assert "multicall shim" in source

    # Explicit config pointing to indirect candidate must fail without
    # falling back to PATH
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)
    exe, source = resolve_agy_executable(
        configured_cmd=str(shim_link), path_env=str(bin2)
    )
    assert exe is None
    assert "config" in source
    assert "multicall shim" in source


def test_discovery_resolver_success_and_dynamic_switch(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    v1_exe = _make_executable_script(tmp_path / "v1" / "agy")
    v2_exe = _make_executable_script(tmp_path / "v2" / "agy")

    state_file = tmp_path / "active_manager_state.txt"
    state_file.write_text(str(v1_exe), encoding="utf-8")

    resolver_script = tmp_path / "resolver.py"
    resolver_code = (
        "import sys\n"
        "from pathlib import Path\n"
        f'print(Path("{state_file}").read_text(encoding="utf-8").strip())\n'
    )
    resolver_script.write_text(resolver_code, encoding="utf-8")

    resolver_cmd = [sys.executable, str(resolver_script)]

    # Invocation 1: returns v1
    exe, source = resolve_agy_executable(configured_resolver=resolver_cmd)
    assert exe == v1_exe
    assert source == "resolver"

    # Simulate upgrade/reshim: update state file to v2 without any config modification
    state_file.write_text(str(v2_exe), encoding="utf-8")

    # Invocation 2: returns v2 immediately
    exe, source = resolve_agy_executable(configured_resolver=resolver_cmd)
    assert exe == v2_exe
    assert source == "resolver"


def test_discovery_resolver_error_cases(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    def _make_resolver(code: str) -> list[str]:
        p = tmp_path / f"res_{len(list(tmp_path.glob('res_*.py')))}.py"
        p.write_text(code, encoding="utf-8")
        return [sys.executable, str(p)]

    # 1. Nonzero exit code
    r_fail = _make_resolver(
        "import sys\nsys.stderr.write('manager error')\nsys.exit(2)\n"
    )
    exe, source = resolve_agy_executable(configured_resolver=r_fail)
    assert exe is None
    assert "config (resolver" in source
    assert "failed" in source

    # 2. Empty output
    r_empty = _make_resolver("pass\n")
    exe, source = resolve_agy_executable(configured_resolver=r_empty)
    assert exe is None
    assert "returned empty output" in source

    # 3. Multiple lines
    r_multiline = _make_resolver("print('/path/one')\nprint('/path/two')\n")
    exe, source = resolve_agy_executable(configured_resolver=r_multiline)
    assert exe is None
    assert "returned multiple lines" in source

    # 4. Relative path
    r_rel = _make_resolver("print('relative/path/agy')\n")
    exe, source = resolve_agy_executable(configured_resolver=r_rel)
    assert exe is None
    assert "returned relative path" in source

    # 5. Non-executable output
    non_exe = tmp_path / "not_exe.txt"
    non_exe.write_text("hello", encoding="utf-8")
    r_non_exe = _make_resolver(f"print('{non_exe}')\n")
    exe, source = resolve_agy_executable(configured_resolver=r_non_exe)
    assert exe is None
    assert "not executable" in source

    # 6. Indirect output (returns a symlink)
    real_target = _make_executable_script(tmp_path / "direct" / "agy")
    shim = tmp_path / "shim" / "agy"
    shim.parent.mkdir(parents=True, exist_ok=True)
    shim.symlink_to(real_target)
    r_indirect = _make_resolver(f"print('{shim}')\n")
    exe, source = resolve_agy_executable(configured_resolver=r_indirect)
    assert exe is None
    assert "symlink indirection" in source


def test_discovery_broken_explicit_symlinks(tmp_path: Path, monkeypatch):
    fake_bin = tmp_path / "fake_bin"
    _make_executable_script(fake_bin / "agy")

    broken = tmp_path / "broken_symlink"
    broken.symlink_to(tmp_path / "nonexistent_target")

    # Broken MAGY_AGY_CMD must fail without PATH fallback
    monkeypatch.setenv("MAGY_AGY_CMD", str(broken))
    exe, source = resolve_agy_executable(path_env=str(fake_bin))
    assert exe is None
    assert "MAGY_AGY_CMD" in source
    assert "broken symlink" in source


def test_discovery_broken_path_shims_ignored(tmp_path: Path, monkeypatch):
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    bin1 = tmp_path / "bin1"
    bin1.mkdir(parents=True, exist_ok=True)
    broken = bin1 / "agy"
    broken.symlink_to(tmp_path / "nonexistent_target")

    bin2 = tmp_path / "bin2"
    direct = _make_executable_script(bin2 / "agy")

    path_env = f"{bin1}{os.pathsep}{bin2}"
    exe, source = resolve_agy_executable(path_env=path_env)
    assert exe == direct
    assert source == "PATH"
