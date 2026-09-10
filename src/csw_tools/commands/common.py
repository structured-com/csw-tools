"""Shared helpers for safe mutating commands."""

from __future__ import annotations

from pathlib import Path

import click

from csw_tools.change_control import BackupError
from csw_tools.context import AppContext
from csw_tools.csw_api import CswApi, CswApiError, create_api_client

DEFAULT_BACKUP_DIRECTORY = Path("csw-tools-backups")


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
