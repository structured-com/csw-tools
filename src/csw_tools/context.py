"""Shared services and resolved settings passed to every command.

The root CLI creates one :class:`AppContext` for each invocation and stores it
in Click's ``ctx.obj``. The ``pass_app_context`` decorator then injects that
same object into the selected command. This gives every command consistent
access to configuration, secrets, output streams, and the selected config path without
requiring command modules to initialize those concerns themselves.

The context carries a keyring accessor, not eagerly loaded password values.
Commands retrieve only the secrets they need, when they need them.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

import click
from rich.console import Console

from csw_tools.config import AppConfig
from csw_tools.keyring_store import KeyringStore


@dataclass(slots=True)
class AppContext:
    """Per-invocation dependencies shared by all csw-tools commands.

    A new instance is assembled by the root CLI after configuration precedence
    has been applied. Command modules receive it by using ``@pass_app_context``.
    """

    # The validated common settings and the configuration tables owned by each
    # command. A command can read its table with ``config_for(command_name)``.
    config: AppConfig

    # The normalized path selected by ``--config``, or the OS-native default
    # when no override was provided. For ``init`` this is the file to create or
    # replace; other commands load their configuration from this path.
    config_path: Path

    # A lazy adapter for the operating system keyring. It already contains the
    # resolved service name, but it does not contain API keys or other password
    # values. A command explicitly retrieves a password only when it needs one.
    keyring: KeyringStore

    # Rich output directed to stdout. Use this for the command's normal results
    # so callers can redirect or pipe those results without diagnostic noise.
    console: Console

    # Rich output directed to stderr. Use this for errors, warnings, progress,
    # and other diagnostics that should remain separate from normal results.
    error_console: Console

    def config_for(self, command_name: str) -> Mapping[str, object]:
        """Return configuration owned by one command."""

        return self.config.for_command(command_name)


pass_app_context = click.make_pass_decorator(AppContext)
