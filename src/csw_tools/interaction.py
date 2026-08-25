"""Shared safeguards for commands that require an interactive user."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import cast

import click


def is_interactive_terminal() -> bool:
    """Return whether standard input is attached to an interactive terminal."""

    return click.get_text_stream("stdin").isatty()


def interactive_command[**P, R](
    function: Callable[P, R],
) -> Callable[P, R]:
    """Reject command execution when interactive input is unavailable."""

    @wraps(function)
    def guarded(*args: P.args, **kwargs: P.kwargs) -> R:
        if not is_interactive_terminal():
            raise click.ClickException(
                "This command requires an interactive terminal. "
                "Run it directly from a terminal session."
            )
        return function(*args, **kwargs)

    return cast(Callable[P, R], guarded)
