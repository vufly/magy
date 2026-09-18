import json
import os
import re
import uuid
from pathlib import Path
from typing import Any

from filelock import FileLock

IDENTIFIER_PATTERN = re.compile(r"\A[a-zA-Z0-9_-]+\Z")

WINDOWS_RESERVED_NAMES = {
    "CON",
    "PRN",
    "AUX",
    "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
}


def validate_identifier(name: str, identifier_type: str = "identifier") -> str:
    """Validate that name is a safe single path component."""
    if not isinstance(name, str):
        raise TypeError(
            f"{identifier_type} must be a string, got {type(name).__name__}"
        )
    if not name:
        raise ValueError(f"{identifier_type} cannot be empty")
    if len(name) > 64:
        raise ValueError(
            f"{identifier_type} '{name}' is too long (maximum 64 characters)"
        )
    if "/" in name or "\\" in name:
        raise ValueError(
            f"{identifier_type} '{name}' cannot contain path separators"
        )
    if name in (".", ".."):
        raise ValueError(f"{identifier_type} '{name}' is not allowed")
    if not IDENTIFIER_PATTERN.fullmatch(name):
        raise ValueError(
            f"{identifier_type} '{name}' contains invalid characters "
            "(only alphanumeric, dash, and underscore allowed)"
        )
    if name.upper() in WINDOWS_RESERVED_NAMES:
        raise ValueError(
            f"{identifier_type} '{name}' matches a reserved platform name"
        )
    return name


def validate_profile_name(name: str) -> str:
    return validate_identifier(name, "profile name")


def validate_run_id(run_id: str) -> str:
    return validate_identifier(run_id, "run ID")


def ensure_private_directory(path: Path) -> Path:
    """Create directory and parents with owner-only permissions where supported."""
    path.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path, 0o700)
        except OSError:
            pass
    return path


def ensure_private_file(path: Path) -> Path:
    """Set file permissions to owner read/write where supported."""
    if os.name != "nt" and path.exists():
        try:
            os.chmod(path, 0o600)
        except OSError:
            pass
    return path


def atomic_write_json(
    path: Path,
    data: Any,
    *,
    lock: bool = True,
    timeout: float = 10.0,
) -> None:
    """Write data as formatted JSON atomically and securely with optional locking."""
    ensure_private_directory(path.parent)

    encoded = json.dumps(data, indent=2, sort_keys=True).encode("utf-8") + b"\n"
    lock_path = path.with_name(f".{path.name}.lock")
    temp_path = path.with_name(f".{path.name}.tmp.{os.getpid()}.{uuid.uuid4().hex}")

    def _do_write() -> None:
        flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
        if hasattr(os, "O_BINARY"):
            flags |= os.O_BINARY

        mode = 0o600
        fd = os.open(temp_path, flags, mode)
        try:
            total_len = len(encoded)
            offset = 0
            while offset < total_len:
                written = os.write(fd, encoded[offset:])
                if written == 0:
                    raise OSError(
                        "Failed to write to temporary file: 0 bytes written"
                    )
                offset += written
            os.fsync(fd)
        finally:
            os.close(fd)

        ensure_private_file(temp_path)
        os.replace(temp_path, path)

        if os.name != "nt":
            try:
                dir_fd = os.open(
                    path.parent, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0)
                )
                try:
                    os.fsync(dir_fd)
                finally:
                    os.close(dir_fd)
            except OSError:
                pass

    try:
        if lock:
            with FileLock(str(lock_path), timeout=timeout):
                _do_write()
        else:
            _do_write()
    finally:
        if temp_path.exists():
            try:
                temp_path.unlink()
            except OSError:
                pass


def read_json(
    path: Path,
    *,
    lock: bool = False,
    timeout: float = 10.0,
    default: Any = ...,
) -> Any:
    """Read and parse JSON from path, optionally acquiring a lock."""
    if not path.exists():
        if default is not ...:
            return default
        raise FileNotFoundError(f"File not found: {path}")

    lock_path = path.with_name(f".{path.name}.lock")

    def _do_read() -> Any:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)

    if lock:
        with FileLock(str(lock_path), timeout=timeout):
            return _do_read()
    return _do_read()


def get_lock(path: Path, timeout: float = 10.0) -> FileLock:
    """Return a FileLock for synchronizing operations on path."""
    lock_path = path.with_name(f".{path.name}.lock")
    return FileLock(str(lock_path), timeout=timeout)


def update_json(
    path: Path,
    update_fn: Any,
    *,
    timeout: float = 10.0,
    default: Any = ...,
) -> Any:
    """Read, update via update_fn(data), and atomically write under lock."""
    with get_lock(path, timeout=timeout):
        data = read_json(path, lock=False, default=default)
        new_data = update_fn(data)
        atomic_write_json(path, new_data, lock=False)
        return new_data
