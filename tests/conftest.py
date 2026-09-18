import os
import shutil
import stat
import sys
from pathlib import Path
from typing import Generator

import pytest


@pytest.fixture(autouse=True)
def isolate_environment(tmp_path_factory, monkeypatch) -> None:
    """Ensure all tests run with isolated storage roots and no real agy on PATH."""
    root = tmp_path_factory.mktemp("magy_test_root")
    monkeypatch.setenv("MAGY_CONFIG_DIR", str(root / "config"))
    monkeypatch.setenv("MAGY_DATA_DIR", str(root / "data"))
    monkeypatch.setenv("MAGY_STATE_DIR", str(root / "state"))

    # Remove any existing MAGY_AGY_CMD
    monkeypatch.delenv("MAGY_AGY_CMD", raising=False)

    # Sanitize PATH to ensure real agy cannot be discovered by accident
    current_path = os.environ.get("PATH", "")
    paths = current_path.split(os.pathsep)
    filtered_paths = [
        p
        for p in paths
        if not shutil.which("agy", path=p)  # Strip out any directory containing agy
    ]
    monkeypatch.setenv("PATH", os.pathsep.join(filtered_paths))


class FakeAgy:
    def __init__(self, executable: Path, log_file: Path, base_dir: Path):
        self.executable = executable
        self.log_file = log_file
        self.base_dir = base_dir

    def get_invocations(self) -> list[dict]:
        if not self.log_file.exists():
            return []
        import json

        records = []
        for line in self.log_file.read_text(encoding="utf-8").splitlines():
            if line.strip():
                records.append(json.loads(line))
        return records


@pytest.fixture
def fake_agy(tmp_path: Path, monkeypatch) -> Generator[FakeAgy, None, None]:
    """Provide an executable fake Agy harness script and controller."""
    bin_dir = tmp_path / "fake_bin"
    bin_dir.mkdir(parents=True, exist_ok=True)
    log_file = tmp_path / "fake_agy_invocations.jsonl"

    monkeypatch.setenv("FAKE_AGY_LOG_FILE", str(log_file))

    if os.name == "nt":
        script_path = bin_dir / "agy.bat"
        script_content = (
            f'@echo off\r\n"{sys.executable}" -m magy.testing.fake_agy %*\r\n'
        )
        script_path.write_text(script_content, encoding="utf-8")
    else:
        script_path = bin_dir / "agy"
        script_content = (
            f'#!/bin/sh\nexec "{sys.executable}" -m magy.testing.fake_agy "$@"\n'
        )
        script_path.write_text(script_content, encoding="utf-8")
        mode = script_path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        script_path.chmod(mode)

    # Ensure fake_agy can be found on PATH or via MAGY_AGY_CMD if desired
    controller = FakeAgy(
        executable=script_path,
        log_file=log_file,
        base_dir=tmp_path,
    )
    yield controller
