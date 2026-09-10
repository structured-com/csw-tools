"""Convert observed CSW inventory dimensions into static workload labels."""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path

import click
from rich.table import Table

from csw_tools.change_control import backup_path, load_backup, new_backup, write_backup
from csw_tools.commands.common import (
    DEFAULT_BACKUP_DIRECTORY,
    api_for,
    command_error,
    require_apply_for_rollback,
)
from csw_tools.context import AppContext, pass_app_context
from csw_tools.csw_api import CswApi
from csw_tools.dashboard_context import dashboard_command
from csw_tools.interaction import interactive_command

COMMAND_NAME = "convert-labels"


def parse_label_mapping(value: str) -> tuple[str, str]:
    source, separator, target = value.partition(":")
    source = source.strip()
    target = target.strip() if separator else source.removeprefix("user_")
    if not source or not target:
        raise click.BadParameter("use SOURCE or SOURCE:TARGET", param_hint="--label")
    return source, target


def plan_changes(
    api: CswApi,
    mappings: list[tuple[str, str]],
    *,
    scope: str | None,
    page_size: int,
) -> list[dict[str, object]]:
    dimensions = list(dict.fromkeys(["ip", *(source for source, _ in mappings)]))
    operations: list[dict[str, object]] = []
    for workload in api.inventory(
        dimensions=dimensions, scope=scope, page_size=page_size
    ):
        ip = workload.get("ip")
        if not isinstance(ip, str) or not ip:
            continue
        additions = {
            target: workload[source]
            for source, target in mappings
            if workload.get(source) not in (None, "")
        }
        if not additions:
            continue
        before = api.get_static_label(ip)
        after = dict(before or {})
        after.update(additions)
        if before == after:
            continue
        operations.append({"ip": ip, "before": before, "after": after})
    return operations


def rollback_changes(api: CswApi, backup: Mapping[str, object]) -> int:
    operations = backup["operations"]
    assert isinstance(operations, list)
    restored = 0
    for operation in reversed(operations):
        if not isinstance(operation, dict) or not operation.get("attempted"):
            continue
        ip = operation.get("ip")
        before = operation.get("before")
        if not isinstance(ip, str):
            continue
        if before is None:
            api.delete_static_label(ip)
        elif isinstance(before, dict):
            api.set_static_label(ip, before)
        restored += 1
    return restored


@click.command(COMMAND_NAME, context_settings={"max_content_width": 100})
@click.option(
    "--label",
    "labels",
    multiple=True,
    metavar="SOURCE[:TARGET]",
    help=(
        "Inventory field to persist. Repeat for multiple fields. SOURCE:TARGET "
        "renames the static label; SOURCE alone uses the same name (with a "
        "leading 'user_' removed)."
    ),
)
@click.option(
    "--scope",
    help="Limit workloads to this exact fully qualified implied scope name.",
)
@click.option(
    "--page-size",
    type=click.IntRange(min=1),
    default=500,
    show_default=True,
    help="Inventory records requested per API page.",
)
@click.option(
    "--backup-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=DEFAULT_BACKUP_DIRECTORY,
    show_default=True,
    help="Directory for automatic JSON backups created before changes.",
)
@click.option(
    "--apply/--dry-run",
    default=False,
    show_default=True,
    help="Apply changes, or only display the planned changes.",
)
@click.option(
    "--rollback",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help="Restore labels from a backup created by this command; requires --apply.",
)
@pass_app_context
@interactive_command
@dashboard_command
def command(
    app: AppContext,
    labels: tuple[str, ...],
    scope: str | None,
    page_size: int,
    backup_dir: Path,
    apply: bool,
    rollback: Path | None,
) -> None:
    """(DEV/TESTING) Persist dynamic inventory fields as static workload labels.

    Static labels are written against each workload IP. Existing static labels
    are preserved; only requested keys are added or updated. Scope membership is
    implied by the resulting workload labels and can be limited with --scope.

    The default is --dry-run. --apply writes a timestamped JSON backup before
    the first update. Use --apply --rollback BACKUP to restore every applied
    record, including removing records newly created by the original run.

    \b
    Examples:
      csw-tools -d acme convert-labels --label hostname
      csw-tools -d acme convert-labels --label hostname:asset_name --label os --apply
      csw-tools -d acme convert-labels --apply --rollback BACKUP.json
    """

    try:
        require_apply_for_rollback(apply, rollback)
        api = api_for(app)
        assert app.dashboard is not None
        if rollback is not None:
            backup = load_backup(
                rollback, command=COMMAND_NAME, dashboard=app.dashboard
            )
            count = rollback_changes(api, backup)
            app.console.print(
                f"[green]Restored {count} workload label record(s).[/green]"
            )
            return
        if not labels:
            raise click.UsageError("At least one --label is required")

        mappings = [parse_label_mapping(value) for value in labels]
        operations = plan_changes(api, mappings, scope=scope, page_size=page_size)
        table = Table("Workload", "Before", "After", title="Static label changes")
        for operation in operations:
            table.add_row(
                str(operation["ip"]),
                str(operation["before"] or {}),
                str(operation["after"]),
            )
        app.console.print(table)
        if not operations:
            app.console.print("No label changes are required.")
            return
        if not apply:
            app.console.print("[yellow]Dry run: no labels were changed.[/yellow]")
            return

        backup = new_backup(
            command=COMMAND_NAME, dashboard=app.dashboard, operations=operations
        )
        path = backup_path(backup_dir, COMMAND_NAME)
        write_backup(path, backup)
        for operation in operations:
            operation["attempted"] = True
            write_backup(path, backup)
            api.set_static_label(str(operation["ip"]), operation["after"])
            operation["applied"] = True
            write_backup(path, backup)
        app.console.print(f"[green]Updated {len(operations)} workload(s).[/green]")
        app.console.print(f"Backup: {path}")
    except click.ClickException:
        raise
    except Exception as exc:
        raise command_error(exc) from exc
