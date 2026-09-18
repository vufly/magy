import json
import os
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from filelock import Timeout
from platformdirs import PlatformDirs

from magy.storage import (
    atomic_write_json,
    ensure_private_directory,
    read_json,
    safe_expand_path,
)

APP_NAME = "magy"


def get_config_dir(create: bool = True) -> Path:
    """Return the user configuration directory for magy."""
    env_dir = os.environ.get("MAGY_CONFIG_DIR")
    if env_dir:
        path = safe_expand_path(env_dir)
    else:
        dirs = PlatformDirs(appname=APP_NAME, appauthor=False)
        path = safe_expand_path(dirs.user_config_dir)
    if create:
        ensure_private_directory(path)
    return path


def get_data_dir(create: bool = True) -> Path:
    """Return the user data directory for magy."""
    env_dir = os.environ.get("MAGY_DATA_DIR")
    if env_dir:
        path = safe_expand_path(env_dir)
    else:
        dirs = PlatformDirs(appname=APP_NAME, appauthor=False)
        path = safe_expand_path(dirs.user_data_dir)
    if create:
        ensure_private_directory(path)
    return path


def get_state_dir(create: bool = True) -> Path:
    """Return the user state directory for magy."""
    env_dir = os.environ.get("MAGY_STATE_DIR")
    if env_dir:
        path = safe_expand_path(env_dir)
    else:
        dirs = PlatformDirs(appname=APP_NAME, appauthor=False)
        path = safe_expand_path(dirs.user_state_dir)
    if create:
        ensure_private_directory(path)
    return path


@dataclass
class MagyConfig:
    agy_cmd: str | None = None

    def __post_init__(self) -> None:
        if self.agy_cmd is not None:
            if not isinstance(self.agy_cmd, str):
                raise TypeError(
                    "agy_cmd must be a string or null, got "
                    f"{type(self.agy_cmd).__name__}"
                )
            if not self.agy_cmd.strip():
                raise ValueError("agy_cmd cannot be an empty string")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "MagyConfig":
        if not isinstance(data, dict):
            raise TypeError(
                f"Configuration must be a JSON object, got {type(data).__name__}"
            )
        return cls(agy_cmd=data.get("agy_cmd"))


@dataclass
class ConfigLoadResult:
    config: MagyConfig
    error: str | None = None


def get_config_file_path() -> Path:
    return get_config_dir(create=False) / "config.json"


def load_config_result() -> ConfigLoadResult:
    """Load configuration from config.json, capturing any configuration error."""
    path = None
    try:
        path = get_config_file_path()
        if not path.exists():
            return ConfigLoadResult(config=MagyConfig(), error=None)

        data = read_json(path, lock=True, timeout=2.0)
        cfg = MagyConfig.from_dict(data)
        return ConfigLoadResult(config=cfg, error=None)
    except json.JSONDecodeError as e:
        target = f" in config file {path}" if path else ""
        return ConfigLoadResult(
            config=MagyConfig(),
            error=(
                f"Malformed JSON{target}: {e.msg} "
                f"(line {e.lineno}, col {e.colno})"
            ),
        )
    except (TypeError, ValueError) as e:
        target = f" in {path}" if path else ""
        return ConfigLoadResult(
            config=MagyConfig(),
            error=f"Invalid configuration{target}: {e}",
        )
    except (TimeoutError, Timeout):
        target = f": {path}" if path else ""
        return ConfigLoadResult(
            config=MagyConfig(),
            error=f"Timed out acquiring lock on config file{target}",
        )
    except PermissionError as e:
        target = f" ({path})" if path else ""
        return ConfigLoadResult(
            config=MagyConfig(),
            error=f"Permission denied accessing config{target}: {e}",
        )
    except (RuntimeError, OSError) as e:
        target = f" ({path})" if path else ""
        return ConfigLoadResult(
            config=MagyConfig(),
            error=f"Error accessing config{target}: {e}",
        )


def load_config() -> MagyConfig:
    """Load configuration from config.json, returning defaults if not found."""
    return load_config_result().config


def save_config(config: MagyConfig) -> None:
    """Save configuration to config.json atomically."""
    path = get_config_file_path()
    atomic_write_json(path, config.to_dict(), lock=True)
