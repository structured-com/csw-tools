"""Bulk CSW scope creation with backups and rollback."""

from __future__ import annotations

import csv
import json
from dataclasses import dataclass
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
CSV_FIELDS = {
    "short_name",
    "parent",
    "description",
    "query",
    "filter_json",
    "policy_priority",
}


@dataclass(frozen=True)
class QueryToken:
    kind: str
    value: str
    position: int


def tokenize_scope_query(expression: str) -> list[QueryToken]:
    """Tokenize the intentionally small, user-facing scope query language."""

    tokens: list[QueryToken] = []
    position = 0
    while position < len(expression):
        character = expression[position]
        if character.isspace():
            position += 1
            continue
        if character in "(),=":
            kinds = {"(": "LPAREN", ")": "RPAREN", ",": "COMMA", "=": "EQ"}
            tokens.append(QueryToken(kinds[character], character, position))
            position += 1
            continue
        if character == "!":
            if position + 1 < len(expression) and expression[position + 1] == "=":
                tokens.append(QueryToken("NE", "!=", position))
                position += 2
                continue
            raise ValueError(
                f"Unexpected '!' at column {position + 1}; use '!=' for inequality"
            )
        if character in {'"', "'"}:
            quote = character
            start = position
            position += 1
            value: list[str] = []
            while position < len(expression):
                character = expression[position]
                if character == quote:
                    position += 1
                    break
                if character == "\\":
                    position += 1
                    if position >= len(expression):
                        raise ValueError(
                            f"Trailing escape in quoted value at column {start + 1}"
                        )
                    value.append(expression[position])
                    position += 1
                    continue
                value.append(character)
                position += 1
            else:
                raise ValueError(
                    f"Unterminated quoted value starting at column {start + 1}"
                )
            tokens.append(QueryToken("VALUE", "".join(value), start))
            continue

        start = position
        while position < len(expression):
            character = expression[position]
            if character.isspace() or character in "(),=!\"'":
                break
            position += 1
        if position == start:
            raise ValueError(
                "Unexpected character "
                f"{expression[position]!r} at column {position + 1}"
            )
        tokens.append(QueryToken("WORD", expression[start:position], start))
    tokens.append(QueryToken("EOF", "", len(expression)))
    return tokens


class ScopeQueryParser:
    """Recursive-descent parser for scope expressions with boolean precedence."""

    def __init__(self, expression: str) -> None:
        self.tokens = tokenize_scope_query(expression)
        self.index = 0

    @property
    def current(self) -> QueryToken:
        return self.tokens[self.index]

    def advance(self) -> QueryToken:
        token = self.current
        self.index += 1
        return token

    def is_keyword(self, keyword: str) -> bool:
        return self.current.kind == "WORD" and self.current.value.upper() == keyword

    def accept_keyword(self, keyword: str) -> bool:
        if not self.is_keyword(keyword):
            return False
        self.advance()
        return True

    def expect(self, kind: str, description: str) -> QueryToken:
        if self.current.kind != kind:
            self.fail(f"Expected {description}")
        return self.advance()

    def fail(self, message: str) -> None:
        token = self.current
        raise ValueError(f"{message} at column {token.position + 1}")

    def parse(self) -> dict[str, object]:
        if self.current.kind == "EOF":
            self.fail("Query cannot be empty")
        result = self.parse_or()
        if self.current.kind != "EOF":
            self.fail(f"Unexpected token {self.current.value!r}")
        return result

    def parse_or(self) -> dict[str, object]:
        result = self.parse_and()
        while self.accept_keyword("OR"):
            result = combine_filters("or", result, self.parse_and())
        return result

    def parse_and(self) -> dict[str, object]:
        result = self.parse_not()
        while self.accept_keyword("AND"):
            result = combine_filters("and", result, self.parse_not())
        return result

    def parse_not(self) -> dict[str, object]:
        if self.accept_keyword("NOT"):
            return {"type": "not", "filter": self.parse_not()}
        return self.parse_primary()

    def parse_primary(self) -> dict[str, object]:
        if self.current.kind == "LPAREN":
            self.advance()
            result = self.parse_or()
            self.expect("RPAREN", "')'")
            return result
        return self.parse_condition()

    def parse_condition(self) -> dict[str, object]:
        field_token = self.expect("WORD", "a label or API field")
        field = normalize_scope_field(field_token.value, field_token.position)

        if self.current.kind in {"EQ", "NE"}:
            operator = "eq" if self.advance().kind == "EQ" else "ne"
            return {
                "type": operator,
                "field": field,
                "value": self.parse_value(),
            }
        if self.accept_keyword("EQ"):
            return {"type": "eq", "field": field, "value": self.parse_value()}
        if self.accept_keyword("NE"):
            return {"type": "ne", "field": field, "value": self.parse_value()}
        if self.accept_keyword("CONTAINS"):
            return {
                "type": "contains",
                "field": field,
                "value": self.parse_value(),
            }
        if self.accept_keyword("REGEX"):
            return {
                "type": "regex",
                "field": field,
                "value": self.parse_value(),
            }
        if self.accept_keyword("IN"):
            return {"type": "in", "field": field, "values": self.parse_values()}
        self.fail("Expected =, !=, EQ, NE, IN, CONTAINS, or REGEX")

    def parse_value(self) -> str:
        if self.current.kind not in {"WORD", "VALUE"}:
            self.fail("Expected a value")
        return self.advance().value

    def parse_values(self) -> list[str]:
        self.expect("LPAREN", "'(' after IN")
        values = [self.parse_value()]
        while self.current.kind == "COMMA":
            self.advance()
            values.append(self.parse_value())
        self.expect("RPAREN", "')' after IN values")
        return values


def normalize_scope_field(field: str, position: int = 0) -> str:
    if field.startswith("*"):
        if len(field) == 1:
            raise ValueError(
                f"Label name is missing after '*' at column {position + 1}"
            )
        return f"user_{field[1:]}"
    return field


def combine_filters(
    operator: str, left: dict[str, object], right: dict[str, object]
) -> dict[str, object]:
    filters: list[object] = []
    for item in (left, right):
        if item.get("type") == operator and isinstance(item.get("filters"), list):
            filters.extend(item["filters"])
        else:
            filters.append(item)
    return {"type": operator, "filters": filters}


def parse_scope_query(expression: str) -> dict[str, object]:
    """Translate a friendly scope expression into a CSW short_query object."""

    return ScopeQueryParser(expression).parse()


def read_scope_csv(path: Path) -> list[dict[str, object]]:
    try:
        with path.open(encoding="utf-8-sig", newline="") as source:
            reader = csv.DictReader(source)
            fields = set(reader.fieldnames or ())
            missing = {"short_name", "parent"} - fields
            unknown = fields - CSV_FIELDS
            if missing:
                raise ValueError(
                    f"CSV is missing column(s): {', '.join(sorted(missing))}"
                )
            if unknown:
                raise ValueError(
                    f"CSV has unknown column(s): {', '.join(sorted(unknown))}"
                )
            if not {"query", "filter_json"} & fields:
                raise ValueError("CSV must include a query or filter_json column")
            rows: list[dict[str, object]] = []
            for number, row in enumerate(reader, start=2):
                short_name = (row.get("short_name") or "").strip()
                parent = (row.get("parent") or "").strip()
                if not short_name or not parent:
                    raise ValueError(
                        f"CSV row {number}: short_name and parent are required"
                    )
                query = (row.get("query") or "").strip()
                filter_json = (row.get("filter_json") or "").strip()
                if query and filter_json:
                    raise ValueError(
                        f"CSV row {number}: use query or filter_json, not both"
                    )
                if query:
                    try:
                        short_query = parse_scope_query(query)
                    except ValueError as exc:
                        raise ValueError(
                            f"CSV row {number}: invalid query: {exc}"
                        ) from exc
                    filter_input = query
                elif filter_json:
                    try:
                        short_query = json.loads(filter_json)
                    except json.JSONDecodeError as exc:
                        raise ValueError(
                            f"CSV row {number}: filter_json is invalid JSON"
                        ) from exc
                    if not isinstance(short_query, dict):
                        raise ValueError(
                            f"CSV row {number}: filter_json must be a JSON object"
                        )
                    filter_input = "filter_json"
                else:
                    raise ValueError(
                        f"CSV row {number}: query or filter_json is required"
                    )
                operation: dict[str, object] = {
                    "short_name": short_name,
                    "parent": parent,
                    "name": f"{parent}:{short_name}",
                    "description": (row.get("description") or "").strip(),
                    "short_query": short_query,
                    "filter_input": filter_input,
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
      short_name,parent,description,query,filter_json,policy_priority

    short_name is the new scope's local name. parent is the exact, case-sensitive,
    fully qualified parent scope name. description and policy_priority are optional.
    Supply exactly one filter per row: query is the friendly form; filter_json is a
    quoted CSW short_query object for advanced use. Parent rows must precede children.

    Friendly query syntax supports =, !=, EQ, NE, IN, CONTAINS, REGEX, AND, OR,
    NOT, and parentheses. Precedence is NOT, then AND, then OR. A UI label such as
    *Env becomes the API field user_Env. Quote values that contain spaces, commas,
    parentheses, or operator words. In CSV, double embedded double quotes.

    \b
    Friendly query examples:
      *Env = Prod
      *Env = Prod AND *App IN (App1, App2)
      (*Env = Prod AND *App = App1) OR (*Env = Test AND *App = App2)
      NOT (*Lifecycle = Retired OR *Owner = "Shared Services")

    \b
    CSV example:
      short_name,parent,description,query,filter_json,policy_priority
      ProdApps,Tetration,Production apps,"*Env = Prod AND *App IN (App1, App2)",,100
      Shared,Tetration,Shared services,*Owner = 'Shared Services',,

    Legacy filter_json remains supported when query is blank. Because it is embedded
    JSON, quote the CSV field and double each JSON quote per normal CSV escaping.

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
        table = Table(
            "Scope",
            "Parent",
            "Input",
            "Generated short_query",
            "Result",
            title="Scope creation plan",
        )
        for operation in operations:
            table.add_row(
                str(operation["name"]),
                str(operation["parent"]),
                str(operation["filter_input"]),
                json.dumps(operation["short_query"], separators=(",", ":")),
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
