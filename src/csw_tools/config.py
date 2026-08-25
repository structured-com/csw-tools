"""Load configuration and resolve values from supported input sources."""

from __future__ import annotations

import sys
import tomllib
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast

import click

from csw_tools.config_defaults import (
    APP_NAME,
    CONFIG_FILENAME,
    CONFIG_SECTION_NAMES,
)


class ConfigError(ValueError):
    """Raised when configuration cannot be loaded or resolved safely."""


@dataclass(frozen=True, slots=True)
class CommonConfig:
    """Configuration shared by every command."""

    keyring_service_name: str | None = None


@dataclass(frozen=True, slots=True)
class AppConfig:
    """Validated common configuration and per-command defaults."""

    common: CommonConfig = field(default_factory=CommonConfig)
    command_defaults: Mapping[str, Mapping[str, object]] = field(default_factory=dict)
    source_path: Path | None = None

    def for_command(self, command_name: str) -> Mapping[str, object]:
        """Return configuration owned by one command."""

        return self.command_defaults.get(command_name, {})

    def as_click_default_map(self) -> dict[str, dict[str, object]]:
        """Return command configuration in Click's nested default-map shape."""

        return {
            command_name: dict(values)
            for command_name, values in self.command_defaults.items()
        }


def default_config_path() -> Path:
    """Return the OS-native per-user configuration path."""

    return Path(click.get_app_dir(APP_NAME)) / CONFIG_FILENAME


def load_config(path: Path | None = None, *, explicit: bool = False) -> AppConfig:
    """Load and validate a TOML configuration file.

    The default per-user file is optional. An explicitly selected path must exist.
    """

    config_path = (path if path is not None else default_config_path()).expanduser()

    if not config_path.exists():
        if explicit:
            raise ConfigError(f"Configuration file does not exist: {config_path}")
        return AppConfig()

    if not config_path.is_file():
        raise ConfigError(f"Configuration path is not a file: {config_path}")

    try:
        with config_path.open("rb") as config_file:
            raw_config = tomllib.load(config_file)
    except tomllib.TOMLDecodeError as exc:
        raise ConfigError(f"Invalid TOML in {config_path}: {exc}") from exc
    except OSError as exc:
        raise ConfigError(f"Could not read configuration file {config_path}") from exc

    unknown_sections = sorted(set(raw_config) - set(CONFIG_SECTION_NAMES))
    if unknown_sections:
        section_list = ", ".join(unknown_sections)
        raise ConfigError(f"Unknown configuration section(s): {section_list}")

    for section_name, section in raw_config.items():
        if not isinstance(section, dict):
            raise ConfigError(
                f"Configuration section [{section_name}] must be a TOML table"
            )

    common_values = raw_config.get("common", {})
    unknown_common_keys = sorted(set(common_values) - {"keyring_service_name"})
    if unknown_common_keys:
        key_list = ", ".join(unknown_common_keys)
        raise ConfigError(f"Unknown key(s) in [common]: {key_list}")

    keyring_service_name = common_values.get("keyring_service_name")
    if keyring_service_name is not None:
        if not isinstance(keyring_service_name, str):
            raise ConfigError("[common].keyring_service_name must be a string")
        keyring_service_name = keyring_service_name.strip()
        if not keyring_service_name:
            raise ConfigError("[common].keyring_service_name cannot be empty")

    command_defaults = {
        section_name: dict(cast(dict[str, object], raw_config.get(section_name, {})))
        for section_name in CONFIG_SECTION_NAMES
        if section_name != "common"
    }

    return AppConfig(
        common=CommonConfig(keyring_service_name=keyring_service_name),
        command_defaults=command_defaults,
        source_path=config_path,
    )


def resolve_setting[T](
    *,
    name: str,
    cli_value: T | None = None,
    config_value: T | None = None,
    default: T | None = None,
    required: bool = False,
    allow_input: bool = True,
    input_is_tty: bool | None = None,
    prompt: Callable[[str], T] | None = None,
) -> T | None:
    """Resolve one setting according to the csw-tools precedence policy."""

    for value in (cli_value, config_value, default):
        if value is not None:
            return value

    if not required:
        return None

    if not allow_input:
        raise ConfigError(
            f"Missing required value '{name}'; prompting is disabled by --no-input"
        )

    is_tty = sys.stdin.isatty() if input_is_tty is None else input_is_tty
    if not is_tty:
        raise ConfigError(
            f"Missing required value '{name}'; interactive input is unavailable"
        )

    prompt_value = prompt if prompt is not None else click.prompt
    return cast(T, prompt_value(name))
