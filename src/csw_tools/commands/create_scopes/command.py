"""Bulk CSW scope creation with backups and rollback."""

from __future__ import annotations

import csv
import json
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

COMMAND_NAME = "create-scopes"
CSV_FIELDS = {"short_name", "parent", "description", "filter_json", "policy_priority"}


def read_scope_csv(path: Path) -> list[dict[str, object]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = set(reader.fieldnames or ())
            missing = {"short_name", "parent", "filter_json"} - fields
            unknown = fields - CSV_FIELDS
            if missing:
                raise ValueError(
                    f"CSV is missing column(s): {', '.join(sorted(missing))}"
                )
            if unknown:
                raise ValueError(
                    f"CSV has unknown column(s): {', '.join(sorted(unknown))}"
                )
            rows: list[dict[str, object]] = []
            for number, row in enumerate(reader, start=2):
                short_name = (row.get("short_name") or "").strip()
                parent = (row.get("parent") or "").strip()
                if not short_name or not parent:
                    raise ValueError(
                        f"CSV row {number}: short_name and parent are required"
                    )
                try:
                    short_query = json.loads(row.get("filter_json") or "")
                except json.JSONDecodeError as exc:
                    raise ValueError(
                        f"CSV row {number}: filter_json is invalid JSON"
                    ) from exc
                if not isinstance(short_query, dict):
                    raise ValueError(
                        f"CSV row {number}: filter_json must be a JSON object"
                    )
                operation: dict[str, object] = {
                    "short_name": short_name,
                    "parent": parent,
                    "name": f"{parent}:{short_name}",
                    "description": (row.get("description") or "").strip(),
                    "short_query": short_query,
                }
                priority = (row.get("policy_priority") or "").strip()
                if priority:
                    try:
                        operation["policy_priority"] = int(priority)
                    except ValueError as exc:
                        raise ValueError(
                            f"CSV row {number}: policy_priority must be an integer"
                        ) from exc
                rows.append(operation)
    except OSError as exc:
        raise ValueError(f"Could not read CSV file: {path}") from exc
    return rows


def validate_scope_plan(
    rows: list[dict[str, object]], existing: list[dict[str, object]]
) -> list[dict[str, object]]:
    known_names = {
        scope["name"] for scope in existing if isinstance(scope.get("name"), str)
    }
    planned_names: set[str] = set()
    operations: list[dict[str, object]] = []
    for row in rows:
        name = str(row["name"])
        parent = str(row["parent"])
        if name in known_names or name in planned_names:
            row["skip"] = "scope already exists or is duplicated"
        elif parent not in known_names and parent not in planned_names:
            raise ValueError(
                f"Parent scope '{parent}' must exist or appear earlier in the CSV"
            )
        else:
            planned_names.add(name)
        operations.append(row)
    return operations


def rollback_scopes(api: CswApi, backup: dict[str, object]) -> int:
    operations = backup["operations"]
    assert isinstance(operations, list)
    scopes_by_name = {
        str(scope["name"]): scope
        for scope in api.get_scopes()
        if isinstance(scope.get("name"), str)
    }
    removed = 0
    for operation in reversed(operations):
        if not isinstance(operation, dict):
            continue
        scope_id = operation.get("created_id")
        if not isinstance(scope_id, str) and operation.get("attempted"):
            current = scopes_by_name.get(str(operation.get("name")))
            if isinstance(current, dict):
                scope_id = current.get("id")
        if isinstance(scope_id, str):
            api.delete_scope(scope_id)
            removed += 1
    return removed


@click.command(COMMAND_NAME, context_settings={"max_content_width": 110})
@click.argument(
    "csv_file",
    required=False,
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
)
@click.option(
    "--backup-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=DEFAULT_BACKUP_DIRECTORY,
    show_default=True,
    help="Directory for the automatic JSON backup created before scope creation.",
)
@click.option(
    "--apply/--dry-run",
    default=False,
    show_default=True,
    help="Create the scopes, or only validate and display the creation plan.",
)
@click.option(
    "--rollback",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help=(
        "Delete scopes created by a prior backup, in child-first order; "
        "requires --apply."
    ),
)
@pass_app_context
@interactive_command
@dashboard_command
def command(
    app: AppContext,
    csv_file: Path | None,
    backup_dir: Path,
    apply: bool,
    rollback: Path | None,
) -> None:
    """Create scopes in bulk from CSV_FILE.

    \b
    CSV syntax (UTF-8, one scope per row):
      short_name,parent,description,filter_json,policy_priority

    short_name is the new scope's local name. parent is the exact, case-sensitive,
    fully qualified parent scope name. description and policy_priority are optional.
    filter_json is a quoted JSON object containing the CSW short_query. Double each
    embedded quote according to CSV syntax. Parent rows must precede their children.

    \b
    Example:
      short_name,parent,description,filter_json,policy_priority
      Prod,Tetration,,"{""type"":""eq"",""field"":""user_env"",""value"":""p""}",100
      Web,Tetration:Prod,,"{""type"":""eq"",""field"":""user_tier"",""value"":""w""}",

    Existing fully qualified scope names are skipped. The default is --dry-run.
    --apply creates a timestamped JSON backup before the first scope. The backup is
    updated after each successful creation so --apply --rollback BACKUP can remove
    only scopes created by that run, in child-first order.
    """

    try:
        require_apply_for_rollback(apply, rollback)
        api = api_for(app)
        assert app.dashboard is not None
        if rollback is not None:
            backup = load_backup(
                rollback, command=COMMAND_NAME, dashboard=app.dashboard
            )
            count = rollback_scopes(api, backup)
            app.console.print(f"[green]Removed {count} created scope(s).[/green]")
            return
        if csv_file is None:
            raise click.UsageError("CSV_FILE is required unless --rollback is used")

        existing = api.get_scopes()
        operations = validate_scope_plan(read_scope_csv(csv_file), existing)
        table = Table("Scope", "Parent", "Result", title="Scope creation plan")
        for operation in operations:
            table.add_row(
                str(operation["name"]),
                str(operation["parent"]),
                str(operation.get("skip", "create")),
            )
        app.console.print(table)
        pending = [operation for operation in operations if "skip" not in operation]
        if not pending:
            app.console.print("No scopes need to be created.")
            return
        if not apply:
            app.console.print("[yellow]Dry run: no scopes were created.[/yellow]")
            return

        backup = new_backup(
            command=COMMAND_NAME, dashboard=app.dashboard, operations=operations
        )
        path = backup_path(backup_dir, COMMAND_NAME)
        write_backup(path, backup)
        scopes_by_name = {
            str(scope["name"]): scope
            for scope in existing
            if isinstance(scope.get("name"), str)
        }
        for operation in pending:
            parent = scopes_by_name[str(operation["parent"])]
            parent_id = parent.get("id")
            if not isinstance(parent_id, str):
                raise ValueError(f"Parent scope '{operation['parent']}' has no ID")
            payload: dict[str, object] = {
                "short_name": operation["short_name"],
                "description": operation["description"],
                "short_query": operation["short_query"],
                "parent_app_scope_id": parent_id,
            }
            if "policy_priority" in operation:
                payload["policy_priority"] = operation["policy_priority"]
            operation["attempted"] = True
            write_backup(path, backup)
            created = api.create_scope(payload)
            scope_id = created.get("id")
            if not isinstance(scope_id, str):
                raise ValueError(f"Created scope '{operation['name']}' has no ID")
            operation["created_id"] = scope_id
            scopes_by_name[str(operation["name"])] = {
                **created,
                "id": scope_id,
                "name": operation["name"],
            }
            write_backup(path, backup)
        app.console.print(f"[green]Created {len(pending)} scope(s).[/green]")
        app.console.print(f"Backup: {path}")
    except click.ClickException:
        raise
    except Exception as exc:
        raise command_error(exc) from exc
