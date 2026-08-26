import json
from datetime import UTC, datetime, timedelta
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
    read_scope_csv,
    rollback_scopes,
    validate_scope_plan,
)
from csw_tools.dashboard import normalize_dashboard


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
    assert "short_name,parent,description,filter_json,policy_priority" in result.output
    assert "fully qualified parent" in result.output


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


def test_scope_csv_rejects_child_before_parent(tmp_path: Path) -> None:
    csv_file = tmp_path / "scopes.csv"
    csv_file.write_text(
        'short_name,parent,filter_json\nWeb,Tetration:Prod,"{""type"":""eq""}"\n',
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="appear earlier"):
        validate_scope_plan(
            read_scope_csv(csv_file), [{"id": "root", "name": "Tetration"}]
        )


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
