"""Shared runtime context passed from the root CLI to each command."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import click
from rich.console import Console

from csw_tools.config import AppConfig
from csw_tools.keyring_store import KeyringStore


@dataclass(slots=True)
class AppContext:
    """Services and settings shared by all csw-tools commands."""

    config: AppConfig
    keyring: KeyringStore
    console: Console
    error_console: Console
    allow_input: bool

    def config_for(self, command_name: str) -> Mapping[str, object]:
        """Return configuration owned by one command."""

        return self.config.for_command(command_name)


pass_app_context = click.make_pass_decorator(AppContext)
