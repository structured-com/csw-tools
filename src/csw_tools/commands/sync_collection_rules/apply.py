"""Persist payload history and journal a single complete-list update."""

import click
from rich.text import Text

from csw_tools.change_control import (
    BackupError,
    backup_path,
    new_backup,
    write_backup,
)
from csw_tools.commands.sync_collection_rules.api import CollectionRulesApi
from csw_tools.context import AppContext
from csw_tools.csw_api import CswApiError

COMMAND_NAME = "sync-collection-rules"


def apply_collection_rules(
    app: AppContext,
    api: CollectionRulesApi,
    *,
    root_app_scope_name: str,
    vrf_name: str,
    current: list[dict[str, object]],
    additions: list[dict[str, object]],
) -> None:
    """Save both payloads and an attempt record before making one POST."""

    assert app.dashboard is not None
    path = backup_path(app.output_dir, COMMAND_NAME)
    current_path = path.with_stem(f"{path.stem}-current")
    uploaded_path = path.with_stem(f"{path.stem}-uploaded")
    uploaded = additions + current
    operation: dict[str, object] = {
        "attempted": True,
        "status": "attempted",
        "added_rules": len(additions),
    }
    journal = new_backup(
        command=COMMAND_NAME, dashboard=app.dashboard, operations=[operation]
    )
    journal.update(
        root_app_scope_name=root_app_scope_name,
        vrf_name=vrf_name,
        current_payload=current_path.name,
        uploaded_payload=uploaded_path.name,
    )
    write_backup(current_path, current)
    write_backup(uploaded_path, uploaded)
    write_backup(path, journal)
    app.console.print(
        Text(
            f"Current payload: {current_path}\n"
            f"Uploaded payload: {uploaded_path}\nJournal: {path}"
        )
    )

    try:
        api.update(vrf_name, uploaded)
    except (CswApiError, ValueError) as exc:
        operation.update(status="unverified", error=str(exc))
        message = (
            f"{exc}. POST outcome is unverified; the server may have applied "
            "changes. Inspect CSW before retrying. No automatic retry or "
            "rollback was attempted."
        )
        try:
            write_backup(path, journal)
        except BackupError as save_exc:
            message += f" {save_exc}; the journal still records an attempted POST."
        raise click.ClickException(message) from exc

    operation["status"] = "verified"
    try:
        write_backup(path, journal)
    except BackupError as exc:
        raise click.ClickException(
            f"The POST response confirmed the update, but {exc}. "
            "The journal still records an attempted POST; inspect CSW before "
            "retrying."
        ) from exc
