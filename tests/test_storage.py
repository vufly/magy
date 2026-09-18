import concurrent.futures
import os
import stat
from pathlib import Path

import pytest

from magy.storage import (
    atomic_write_json,
    ensure_private_directory,
    ensure_private_file,
    get_lock,
    read_json,
    update_json,
    validate_identifier,
    validate_profile_name,
    validate_run_id,
)


def test_validate_identifier_valid():
    assert validate_identifier("default") == "default"
    assert validate_identifier("profile-1") == "profile-1"
    assert validate_identifier("run_123_abc") == "run_123_abc"
    assert validate_identifier("A") == "A"
    assert validate_identifier("0" * 64) == "0" * 64


@pytest.mark.parametrize(
    "invalid_name",
    [
        "",
        " ",
        "a/b",
        "a\\b",
        "..",
        ".",
        "name with space",
        "name@domain",
        "profile!",
        "run:1",
        "valid\n",
        "valid\r",
        "valid\r\n",
        "val\nid",
        "val\tid",
        "val\x00id",
        "val\x1fid",
        "val\x7fid",
        "val\bid",
        "a" * 65,
        "con",
        "PRN",
        "aux",
        "NUL",
        "com1",
        "lpt9",
    ],
)
def test_validate_identifier_invalid(invalid_name):
    with pytest.raises(ValueError):
        validate_identifier(invalid_name)


def test_validate_identifier_type_error():
    with pytest.raises(TypeError):
        validate_identifier(123)  # type: ignore


def test_validate_profile_and_run_id():
    assert validate_profile_name("dev-1") == "dev-1"
    assert validate_run_id("run-abc") == "run-abc"

    with pytest.raises(ValueError, match="profile name"):
        validate_profile_name("../escape")

    with pytest.raises(ValueError, match="run ID"):
        validate_run_id("../escape")


def test_ensure_private_directory(tmp_path: Path):
    target = tmp_path / "private" / "subdir"
    res = ensure_private_directory(target)
    assert res.exists()
    assert res.is_dir()
    if os.name != "nt":
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_ensure_private_directory_corrects_permissive(tmp_path: Path):
    target = tmp_path / "permissive_dir"
    target.mkdir(mode=0o777, exist_ok=True)
    os.chmod(target, 0o777)
    assert stat.S_IMODE(target.stat().st_mode) == 0o777

    ensure_private_directory(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o700


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_ensure_private_directory_chmod_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "fail_dir"

    def _fail_chmod(path, mode):
        raise OSError("chmod failed on directory")

    monkeypatch.setattr(os, "chmod", _fail_chmod)
    with pytest.raises(PermissionError, match="Failed to enforce private permissions"):
        ensure_private_directory(target)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_ensure_private_directory_insecure_mode_remains(tmp_path: Path, monkeypatch):
    target = tmp_path / "insecure_dir"
    target.mkdir()
    os.chmod(target, 0o777)

    # Monkeypatch chmod to do nothing, leaving mode 0o777
    monkeypatch.setattr(os, "chmod", lambda path, mode: None)
    with pytest.raises(PermissionError, match="insecure permissions 0o777"):
        ensure_private_directory(target)


def test_ensure_private_file(tmp_path: Path):
    target = tmp_path / "private.json"
    target.write_text("{}", encoding="utf-8")
    ensure_private_file(target)
    if os.name != "nt":
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_ensure_private_file_corrects_permissive(tmp_path: Path):
    target = tmp_path / "permissive_file.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o666)
    assert stat.S_IMODE(target.stat().st_mode) == 0o666

    ensure_private_file(target)
    assert stat.S_IMODE(target.stat().st_mode) == 0o600


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_ensure_private_file_chmod_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "fail_file.json"
    target.write_text("{}", encoding="utf-8")

    def _fail_chmod(path, mode):
        raise OSError("chmod failed on file")

    monkeypatch.setattr(os, "chmod", _fail_chmod)
    with pytest.raises(PermissionError, match="Failed to enforce private permissions"):
        ensure_private_file(target)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_ensure_private_file_insecure_mode_remains(tmp_path: Path, monkeypatch):
    target = tmp_path / "insecure_file.json"
    target.write_text("{}", encoding="utf-8")
    os.chmod(target, 0o666)

    # Monkeypatch chmod to do nothing, leaving mode 0o666
    monkeypatch.setattr(os, "chmod", lambda path, mode: None)
    with pytest.raises(PermissionError, match="insecure permissions 0o666"):
        ensure_private_file(target)


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions")
def test_atomic_write_cleanup_on_chmod_failure(tmp_path: Path, monkeypatch):
    target = tmp_path / "data_chmod_fail.json"

    real_chmod = os.chmod

    def _fail_chmod_for_file(path, mode):
        if mode == 0o600:
            raise PermissionError("chmod denied for file")
        return real_chmod(path, mode)

    monkeypatch.setattr(os, "chmod", _fail_chmod_for_file)

    with pytest.raises(PermissionError, match="chmod denied for file"):
        atomic_write_json(target, {"key": "value"}, lock=False)

    assert not target.exists()
    # Check that temporary files are cleaned up
    tmp_files = list(tmp_path.glob(".*.tmp.*"))
    assert len(tmp_files) == 0


def test_atomic_write_and_read_json(tmp_path: Path):
    target = tmp_path / "data.json"
    data = {"key": "value", "count": 42}

    atomic_write_json(target, data, lock=True)
    assert target.exists()
    if os.name != "nt":
        assert stat.S_IMODE(target.stat().st_mode) == 0o600

    loaded = read_json(target, lock=True)
    assert loaded == data


def test_read_json_not_found(tmp_path: Path):
    target = tmp_path / "missing.json"
    with pytest.raises(FileNotFoundError):
        read_json(target)

    assert read_json(target, default={"default": True}) == {"default": True}


def test_atomic_write_interrupted_replacement(tmp_path: Path, monkeypatch):
    target = tmp_path / "state.json"
    atomic_write_json(target, {"initial": "state"})

    class UnserializableObject:
        pass

    # Attempt to write invalid data that fails serialization
    with pytest.raises(TypeError):
        atomic_write_json(target, {"bad": UnserializableObject()})

    # Original file must remain unchanged
    assert read_json(target) == {"initial": "state"}

    # No leftover temporary files
    temp_files = list(tmp_path.glob(".state.json.tmp.*"))
    assert len(temp_files) == 0


def test_atomic_write_concurrent_writers(tmp_path: Path):
    target = tmp_path / "concurrent.json"
    atomic_write_json(target, {"updates": 0})

    iterations = 25

    def worker(worker_id: int):
        for i in range(iterations):
            def modifier(data):
                data["updates"] += 1
                data[f"worker_{worker_id}"] = i
                return data

            update_json(target, modifier)

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, w) for w in range(4)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    final = read_json(target, lock=True)
    assert final["updates"] == iterations * 4


def test_atomic_write_concurrent_raw_writes(tmp_path: Path):
    target = tmp_path / "concurrent_raw.json"
    iterations = 30

    def worker(worker_id: int):
        for i in range(iterations):
            atomic_write_json(
                target,
                {"worker": worker_id, "iteration": i},
                lock=True,
            )

    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
        futures = [executor.submit(worker, w) for w in range(4)]
        for f in concurrent.futures.as_completed(futures):
            f.result()

    final = read_json(target, lock=True)
    assert "worker" in final
    assert "iteration" in final


def test_get_lock(tmp_path: Path):
    target = tmp_path / "lock_test.json"
    lock = get_lock(target)
    with lock:
        assert lock.is_locked
    assert not lock.is_locked


def test_atomic_write_partial_writes(tmp_path: Path, monkeypatch):
    target = tmp_path / "partial.json"
    data = {"large_payload": "x" * 500, "number": 12345}

    real_os_write = os.write

    def partial_write(fd, buf):
        # Force write loop by returning at most 3 bytes per write
        chunk = buf[:3] if len(buf) > 3 else buf
        return real_os_write(fd, chunk)

    monkeypatch.setattr(os, "write", partial_write)

    atomic_write_json(target, data, lock=True)
    assert target.exists()
    loaded = read_json(target, lock=True)
    assert loaded == data


def _mp_storage_worker(target_path_str: str, worker_id: int, iterations: int) -> None:
    path = Path(target_path_str)
    for i in range(iterations):

        def modifier(data):
            data["proc_updates"] += 1
            data[f"worker_{worker_id}"] = i
            return data

        update_json(path, modifier, timeout=30.0)


def test_atomic_write_concurrent_processes(tmp_path: Path):
    target = tmp_path / "proc_concurrent.json"
    atomic_write_json(target, {"proc_updates": 0})

    iterations = 15
    num_processes = 4

    import multiprocessing

    ctx = multiprocessing.get_context("spawn")
    procs = []
    for w in range(num_processes):
        p = ctx.Process(
            target=_mp_storage_worker,
            args=(str(target), w, iterations),
        )
        procs.append(p)
        p.start()

    for p in procs:
        p.join(timeout=30.0)
        assert p.exitcode == 0

    final = read_json(target, lock=True)
    assert final["proc_updates"] == num_processes * iterations
