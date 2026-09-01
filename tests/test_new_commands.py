import json
from datetime import UTC, datetime, timedelta
from importlib import import_module
from pathlib import Path
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from csw_tools.change_control import BackupError, load_backup, new_backup, write_backup
from csw_tools.cli import cli
from csw_tools.commands.clean_stale_labels.command import (
    parse_updated_at,
    plan_cleanup,
    rollback_cleanup,
)
from csw_tools.commands.convert_labels.command import (
    parse_label_mapping,
    plan_changes,
    rollback_changes,
)
from csw_tools.commands.create_scopes.command import (
    apply_scope_operations,
    display_scope_name,
    operation_error,
    parse_scope_query,
    plan_counts,
    read_scope_csv,
    result_counts,
    rollback_scopes,
    scope_error_log_path,
    validate_scope_plan,
    write_scope_error_log,
)
from csw_tools.dashboard import normalize_dashboard

create_scopes_module = import_module("csw_tools.commands.create_scopes.command")


@pytest.mark.parametrize(
    "command_name", ["clean-stale-labels", "convert-labels", "create-scopes"]
)
def test_new_command_help_documents_safety_controls(command_name: str) -> None:
    result = CliRunner().invoke(cli, [command_name, "--help"])

    assert result.exit_code == 0
    assert "--apply" in result.output
    assert "--dry-run" in result.output
    assert "--rollback" in result.output
    assert "backup" in result.output.lower()


def test_create_scopes_help_documents_csv_syntax() -> None:
    result = CliRunner().invoke(cli, ["create-scopes", "--help"])

    assert result.exit_code == 0
    assert "short_name,parent,description,query,filter_json" in result.output
    assert "fully qualified parent" in result.output
    assert "*Env = Prod AND *App IN (App1, App2)" in result.output
    assert "NOT, then AND, then OR" in result.output
    assert "short_query set to null" in result.output
    assert "line number" in result.output
    assert "failed" in result.output


def test_clean_stale_labels_help_documents_default_threshold() -> None:
    result = CliRunner().invoke(cli, ["clean-stale-labels", "--help"])

    assert result.exit_code == 0
    assert "--minimum-age DAYS" in result.output
    assert "30" in result.output
    assert "absent from current inventory" in result.output


def test_parse_label_mapping_supports_rename_and_user_prefix() -> None:
    assert parse_label_mapping("hostname") == ("hostname", "hostname")
    assert parse_label_mapping("user_environment") == (
        "user_environment",
        "environment",
    )
    assert parse_label_mapping("aws_name:asset_name") == ("aws_name", "asset_name")


class ConvertApi:
    def inventory(self, **_kwargs: object):
        return iter(
            [
                {"ip": "10.0.0.1", "hostname": "web-1"},
                {"ip": "10.0.0.2", "hostname": "web-2"},
            ]
        )

    def get_static_label(self, ip: str):
        return {"environment": "prod"} if ip.endswith("1") else None


def test_convert_plan_preserves_existing_labels() -> None:
    operations = plan_changes(
        ConvertApi(), [("hostname", "asset_name")], scope=None, page_size=10
    )

    assert operations[0]["after"] == {
        "environment": "prod",
        "asset_name": "web-1",
    }
    assert operations[1]["before"] is None


class CleanupApi:
    def __init__(self, old: datetime, recent: datetime) -> None:
        self.old = old
        self.recent = recent

    def inventory(self, **_kwargs: object):
        return iter([{"ip": "10.0.0.1"}])

    def search_static_labels(self, _ip_range: str):
        return [
            {
                "key": "10.0.0.1",
                "updatedAt": self.old.timestamp(),
                "value": {"name": "observed"},
            },
            {
                "key": "10.0.0.2",
                "updatedAt": self.old.timestamp(),
                "value": {"name": "stale"},
            },
            {
                "key": "10.0.0.3",
                "updatedAt": self.recent.timestamp(),
                "value": {"name": "recent"},
            },
            {"key": "10.0.0.4", "value": {"name": "unknown"}},
        ]


def test_cleanup_requires_absence_and_minimum_age() -> None:
    now = datetime(2026, 8, 26, tzinfo=UTC)
    operations, missing = plan_cleanup(
        CleanupApi(now - timedelta(days=31), now - timedelta(days=1)),
        minimum_age=30,
        ip_ranges=("0.0.0.0/0",),
        page_size=100,
        now=now,
    )

    assert [operation["ip"] for operation in operations] == ["10.0.0.2"]
    assert missing == 1


@pytest.mark.parametrize(
    ("value", "year"),
    [(1_787_680_000, 2026), (1_787_680_000_000, 2026), ("2026-08-26T00:00:00Z", 2026)],
)
def test_parse_updated_at_accepts_seconds_milliseconds_and_iso(
    value: object, year: int
) -> None:
    parsed = parse_updated_at(value)
    assert parsed is not None
    assert parsed.year == year


def test_read_scope_csv_and_validate_parent_order(tmp_path: Path) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        "short_name,parent,description,filter_json,policy_priority\n"
        'Prod,Tetration,Production,"{""type"":""eq"",""field"":""user_env"",""value"":""prod""}",100\n'
        'Web,Tetration:Prod,,"{""type"":""eq"",""field"":""user_tier"",""value"":""web""}",\n',
        encoding="utf-8",
    )

    rows = read_scope_csv(csv_file)
    operations = validate_scope_plan(rows, [{"id": "root", "name": "Tetration"}])

    assert operations[0]["policy_priority"] == 100
    assert operations[1]["name"] == "Tetration:Prod:Web"


def test_parse_scope_query_supports_compound_conditions() -> None:
    assert parse_scope_query("*Env = Prod AND *App IN (App1, App2)") == {
        "type": "and",
        "filters": [
            {"type": "eq", "field": "user_Env", "value": "Prod"},
            {
                "type": "in",
                "field": "user_App",
                "values": ["App1", "App2"],
            },
        ],
    }


def test_scope_query_parentheses_override_boolean_precedence() -> None:
    assert parse_scope_query("(*Env = Prod OR *Env = Test) AND NOT *App = Retired") == {
        "type": "and",
        "filters": [
            {
                "type": "or",
                "filters": [
                    {"type": "eq", "field": "user_Env", "value": "Prod"},
                    {"type": "eq", "field": "user_Env", "value": "Test"},
                ],
            },
            {
                "type": "not",
                "filter": {
                    "type": "eq",
                    "field": "user_App",
                    "value": "Retired",
                },
            },
        ],
    }


def test_read_scope_csv_accepts_friendly_query_and_quoted_values(
    tmp_path: Path,
) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        "short_name,parent,description,query,filter_json,policy_priority\n"
        'Shared,Tetration,,"*Owner = ""Shared Services""",,\n',
        encoding="utf-8",
    )

    [operation] = read_scope_csv(csv_file)

    assert operation["short_query"] == {
        "type": "eq",
        "field": "user_Owner",
        "value": "Shared Services",
    }
    assert operation["filter_input"] == '*Owner = "Shared Services"'


def test_read_scope_csv_accepts_blank_query_as_none(tmp_path: Path) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        "short_name,parent,description,query,filter_json,policy_priority\n"
        "Empty,Tetration,No filter,,,\n",
        encoding="utf-8",
    )

    [operation] = read_scope_csv(csv_file)

    assert operation["short_query"] is None
    assert operation["filter_input"] == "(none)"
    assert operation["line_number"] == 2


def test_read_scope_csv_continues_after_row_error(tmp_path: Path) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        "short_name,parent,query\n"
        "Broken,Tetration,*Env =\n"
        "Good,Tetration,*Env = Prod\n",
        encoding="utf-8",
    )

    operations = read_scope_csv(csv_file)

    assert operations[0]["line_number"] == 2
    assert "Expected a value" in str(operations[0]["error"])
    assert operations[1]["line_number"] == 3
    assert operations[1]["short_query"] == {
        "type": "eq",
        "field": "user_Env",
        "value": "Prod",
    }


@pytest.mark.parametrize(
    ("query", "message"),
    [
        ("", "Query cannot be empty"),
        ("*Env =", "Expected a value"),
        ("*Env IN ()", "Expected a value"),
        ("(*Env = Prod", "Expected '\\)'"),
        ("*Env ~~ Prod", "Expected ="),
    ],
)
def test_scope_query_reports_invalid_syntax(query: str, message: str) -> None:
    with pytest.raises(ValueError, match=message):
        parse_scope_query(query)


def test_scope_csv_rejects_both_filter_formats_without_stopping(tmp_path: Path) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        "short_name,parent,query,filter_json\n"
        'Prod,Tetration,*Env = Prod,"{""type"":""eq""}"\n',
        encoding="utf-8",
    )

    [operation] = read_scope_csv(csv_file)

    assert operation["error"] == "use query or filter_json, not both"
    assert operation_error(operation).startswith("Line 2:")


def test_scope_csv_rejects_child_before_parent(tmp_path: Path) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        'short_name,parent,filter_json\nWeb,Tetration:Prod,"{""type"":""eq""}"\n',
        encoding="utf-8",
    )

    [operation] = validate_scope_plan(
        read_scope_csv(csv_file), [{"id": "root", "name": "Tetration"}]
    )

    assert "appear earlier" in str(operation["error"])
    assert operation["line_number"] == 2


def test_scope_display_keeps_tail_with_leading_ellipsis() -> None:
    name = "Default:Internal:LongScope:LongApp:App1"

    assert display_scope_name(name, width=15) == "...LongApp:App1"
    assert display_scope_name("Default:App1", width=15) == "Default:App1"


class PartiallyFailingScopeApi:
    def __init__(self) -> None:
        self.payloads: list[dict[str, object]] = []

    def create_scope(self, payload: dict[str, object]) -> dict[str, object]:
        self.payloads.append(payload)
        if payload["short_name"] == "Broken":
            raise ValueError("CSW rejected the scope")
        return {"id": f"id-{payload['short_name']}"}

    def get_scopes(self) -> list[dict[str, object]]:
        return [{"id": "root", "name": "Tetration"}]


def test_scope_apply_continues_after_failure_and_preserves_line_numbers() -> None:
    operations = [
        {
            "line_number": 2,
            "short_name": "Broken",
            "parent": "Tetration",
            "name": "Tetration:Broken",
            "description": "",
            "short_query": None,
            "filter_input": "(none)",
        },
        {
            "line_number": 3,
            "short_name": "Good",
            "parent": "Tetration",
            "name": "Tetration:Good",
            "description": "",
            "short_query": None,
            "filter_input": "(none)",
        },
        {
            "line_number": 4,
            "short_name": "Existing",
            "parent": "Tetration",
            "name": "Tetration:Existing",
            "description": "",
            "short_query": None,
            "filter_input": "(none)",
            "skip": "scope already exists",
        },
    ]
    api = PartiallyFailingScopeApi()
    progress_updates = 0

    def save_progress() -> None:
        nonlocal progress_updates
        progress_updates += 1

    apply_scope_operations(
        api,
        operations,
        [{"id": "root", "name": "Tetration"}],
        save_progress,
    )

    assert [payload["short_name"] for payload in api.payloads] == ["Broken", "Good"]
    assert all(payload["short_query"] is None for payload in api.payloads)
    assert operation_error(operations[0]) == "Line 2: CSW rejected the scope"
    assert operations[1]["created_id"] == "id-Good"
    assert progress_updates == 4
    assert result_counts(operations) == (1, 1, 1)


def test_scope_counts_include_ready_failed_and_skipped() -> None:
    operations = [{}, {"error": "bad"}, {"skip": "exists"}]

    assert plan_counts(operations) == (1, 1, 1)
    assert result_counts(operations) == (0, 1, 1)


def test_scope_error_log_is_unique_and_includes_line_number(tmp_path: Path) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text("short_name,parent\n", encoding="utf-8")
    operations = [
        {
            "line_number": 7,
            "name": "Tetration:Broken",
            "error": "CSW rejected the scope",
        }
    ]
    first = scope_error_log_path(tmp_path)
    second = scope_error_log_path(tmp_path)

    write_scope_error_log(first, csv_file, operations)

    assert first != second
    contents = first.read_text(encoding="utf-8")
    assert "Line 7: CSW rejected the scope" in contents
    assert "Scope: Tetration:Broken" in contents


def test_create_scopes_command_completes_batch_and_reports_summary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        "short_name,parent,query\nBroken,Tetration,*Env =\nGood,Tetration,\n",
        encoding="utf-8",
    )
    api = PartiallyFailingScopeApi()
    monkeypatch.setattr(create_scopes_module, "api_for", lambda _app: api)

    result = CliRunner().invoke(
        cli,
        [
            "--dashboard",
            "test",
            "create-scopes",
            str(csv_file),
            "--apply",
            "--backup-dir",
            str(tmp_path),
        ],
    )

    assert result.exit_code == 1
    assert [payload["short_name"] for payload in api.payloads] == ["Good"]
    assert api.payloads[0]["short_query"] is None
    assert "Line 2: invalid query: Expected a value" in result.output
    assert "Summary: Created 1 | Failed 1 | Skipped 0" in result.output
    [error_log] = tmp_path.glob("create-scopes-errors-*.log")
    assert "Line 2: invalid query: Expected a value" in error_log.read_text(
        encoding="utf-8"
    )


def test_create_scopes_dry_run_displays_long_name_tail(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        "short_name,parent\nApp1,Default:Internal:LongScope:LongApp\n",
        encoding="utf-8",
    )
    api = PartiallyFailingScopeApi()
    monkeypatch.setattr(
        api,
        "get_scopes",
        lambda: [{"id": "parent", "name": "Default:Internal:LongScope:LongApp"}],
    )
    monkeypatch.setattr(create_scopes_module, "api_for", lambda _app: api)

    result = CliRunner().invoke(
        cli,
        ["--dashboard", "test", "create-scopes", str(csv_file), "--dry-run"],
    )

    assert result.exit_code == 0
    assert "...ongScope:LongApp:App1" in result.output
    assert "Summary: Created 0 | Failed 0 | Skipped 0 | Would create 1" in result.output


def test_backup_rejects_other_dashboard(tmp_path: Path) -> None:
    dashboard = normalize_dashboard("one")
    backup = new_backup(command="convert-labels", dashboard=dashboard, operations=[])
    path = tmp_path / "backup.json"
    write_backup(path, backup)

    with pytest.raises(BackupError, match="does not match"):
        load_backup(
            path,
            command="convert-labels",
            dashboard=normalize_dashboard("two"),
        )


def test_label_rollbacks_reverse_only_applied_operations() -> None:
    calls: list[tuple[str, object]] = []
    api = SimpleNamespace(
        set_static_label=lambda ip, attrs: calls.append((ip, attrs)),
        delete_static_label=lambda ip: calls.append((ip, "delete")),
    )
    backup = {
        "operations": [
            {"ip": "10.0.0.1", "before": None, "attempted": True},
            {"ip": "10.0.0.2", "before": {"name": "old"}, "attempted": True},
            {"ip": "10.0.0.3", "before": {}, "attempted": False},
        ]
    }

    assert rollback_changes(api, backup) == 2
    assert calls == [("10.0.0.2", {"name": "old"}), ("10.0.0.1", "delete")]

    calls.clear()
    assert rollback_cleanup(api, backup) == 1
    assert calls == [("10.0.0.2", {"name": "old"})]


def test_scope_rollback_is_child_first() -> None:
    removed: list[str] = []
    api = SimpleNamespace(delete_scope=removed.append, get_scopes=lambda: [])
    backup = {
        "operations": [
            {"name": "Tetration:Prod", "created_id": "parent"},
            {"name": "Tetration:Prod:Web", "created_id": "child"},
        ]
    }

    assert rollback_scopes(api, backup) == 2
    assert removed == ["child", "parent"]


def test_scope_rollback_recovers_creation_interrupted_before_id_was_saved() -> None:
    removed: list[str] = []
    api = SimpleNamespace(
        delete_scope=removed.append,
        get_scopes=lambda: [{"id": "recovered", "name": "Tetration:Prod"}],
    )
    backup = {
        "operations": [
            {"name": "Tetration:Prod", "attempted": True},
            {"name": "Tetration:Untouched"},
        ]
    }

    assert rollback_scopes(api, backup) == 1
    assert removed == ["recovered"]


def test_backup_json_contains_no_client_credentials(tmp_path: Path) -> None:
    dashboard = normalize_dashboard("one")
    backup = new_backup(
        command="convert-labels",
        dashboard=dashboard,
        operations=[{"ip": "10.0.0.1", "before": None}],
    )
    path = tmp_path / "backup.json"
    write_backup(path, backup)

    payload = json.loads(path.read_text(encoding="utf-8"))
    assert set(payload) == {
        "format_version",
        "command",
        "dashboard",
        "created_at",
        "operations",
    }
