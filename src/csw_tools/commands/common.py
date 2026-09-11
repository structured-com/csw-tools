"""Shared helpers for safe mutating commands."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import click

from csw_tools.change_control import BackupError
from csw_tools.context import AppContext
from csw_tools.csw_api import CswApi, CswApiError, create_api_client


def legacy_backup_dir_option[CommandFunction: Callable[..., Any]](
    function: CommandFunction,
) -> CommandFunction:
    """Consume the removed option and report its common replacement."""

    def reject_backup_dir(
        ctx: click.Context, _parameter: click.Parameter, value: Path | None
    ) -> None:
        if value is not None and not ctx.resilient_parsing:
            command_name = ctx.info_name or "COMMAND"
            raise click.UsageError(
                "--backup-dir has moved to the global --output-dir option; use "
                f"'csw-tools --output-dir PATH {command_name}'"
            )

    return click.option(
        "--backup-dir",
        type=click.Path(path_type=Path, file_okay=False),
        callback=reject_backup_dir,
        expose_value=False,
        hidden=True,
    )(function)


def api_for(app: AppContext) -> CswApi:
    try:
        return CswApi(create_api_client(app))
    except CswApiError as exc:
        raise click.ClickException(str(exc)) from exc


def command_error(exc: Exception) -> click.ClickException:
    if isinstance(exc, (CswApiError, BackupError, ValueError)):
        return click.ClickException(str(exc))
    return click.ClickException("The command failed before all changes completed")


def require_apply_for_rollback(apply: bool, rollback: Path | None) -> None:
    if rollback is not None and not apply:
        raise click.UsageError("--rollback requires --apply")
