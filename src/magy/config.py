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
    agy_resolver: list[str] | None = None
    cooldown_rate_limit: float = 60.0
    cooldown_quota: float = 3600.0
    cooldown_timeout: float = 30.0
    cooldown_unknown: float = 15.0
    cooldown_auth: float = 86400.0

    def __post_init__(self) -> None:
        if self.agy_cmd is not None and self.agy_resolver is not None:
            raise ValueError("agy_cmd and agy_resolver are mutually exclusive")

        if self.agy_cmd is not None:
            if not isinstance(self.agy_cmd, str):
                raise TypeError(
                    "agy_cmd must be a string or null, got "
                    f"{type(self.agy_cmd).__name__}"
                )
            if not self.agy_cmd.strip():
                raise ValueError("agy_cmd cannot be an empty string")

        if self.agy_resolver is not None:
            if not isinstance(self.agy_resolver, list):
                raise TypeError(
                    "agy_resolver must be a list of strings, got "
                    f"{type(self.agy_resolver).__name__}"
                )
            if len(self.agy_resolver) == 0:
                raise ValueError("agy_resolver cannot be empty")
            for i, item in enumerate(self.agy_resolver):
                if not isinstance(item, str):
                    raise TypeError(
                        f"agy_resolver[{i}] must be a string, got {type(item).__name__}"
                    )
                if not item.strip():
                    raise ValueError(f"agy_resolver[{i}] cannot be an empty string")

        for attr in (
            "cooldown_rate_limit",
            "cooldown_quota",
            "cooldown_timeout",
            "cooldown_unknown",
            "cooldown_auth",
        ):
            val = getattr(self, attr)
            if not isinstance(val, (int, float)) or val <= 0:
                raise ValueError(f"{attr} must be a positive number, got {val}")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Any) -> "MagyConfig":
        if not isinstance(data, dict):
            raise TypeError(
                f"Configuration must be a JSON object, got {type(data).__name__}"
            )
        try:
            return cls(
                agy_cmd=data.get("agy_cmd"),
                agy_resolver=data.get("agy_resolver"),
                cooldown_rate_limit=float(data.get("cooldown_rate_limit", 60.0)),
                cooldown_quota=float(data.get("cooldown_quota", 3600.0)),
                cooldown_timeout=float(data.get("cooldown_timeout", 30.0)),
                cooldown_unknown=float(data.get("cooldown_unknown", 15.0)),
                cooldown_auth=float(data.get("cooldown_auth", 86400.0)),
            )
        except (ValueError, TypeError) as e:
            raise ValueError(f"Invalid configuration setting: {e}") from e


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
            error=(f"Malformed JSON{target}: {e.msg} (line {e.lineno}, col {e.colno})"),
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
