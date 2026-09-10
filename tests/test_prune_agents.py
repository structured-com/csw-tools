import copy
import json
from importlib import import_module

import pytest
from click.testing import CliRunner

from csw_tools.change_control import BackupError, new_backup, write_backup
from csw_tools.cli import cli
from csw_tools.commands.prune_agents.api import PruneApi
from csw_tools.commands.prune_agents.execution import (
    apply_plan,
    recover,
    validate_recovery,
)
from csw_tools.commands.prune_agents.planner import (
    epoch,
    plan,
    prune_query,
    safe_ips,
    stale,
)
from csw_tools.csw_api import CswRequestError
from csw_tools.dashboard import normalize_dashboard

command_module = import_module("csw_tools.commands.prune_agents.command")
execution_module = import_module("csw_tools.commands.prune_agents.execution")


def agent(uuid="a", checked=100, ip="10.0.0.1"):
    return {
        "uuid": uuid,
        "host_name": "host-" + uuid,
        "last_config_fetch_at": checked,
        "interfaces": [{"ip": ip}],
    }


def eq(ip):
    return {"type": "eq", "field": "ip", "value": ip}


class Backend:
    def __init__(self, shared=False):
        self.sensors = [agent(), agent("b", 300, "10.0.0.2")]
        query = (
            {"type": "or", "filters": [eq("10.0.0.1"), eq("10.0.0.2")]}
            if shared
            else eq("10.0.0.1")
        )
        self.filters = [
            {
                "id": "f",
                "name": "test",
                "short_query": query,
                "app_scope_id": "scope",
                "primary": False,
                "public": False,
            }
        ]
        self.rules = [
            {
                "id": "p",
                "application_id": "app",
                "version": "v1",
                "rank": "DEFAULT",
                "policy_action": "ALLOW",
                "priority": 10,
                "consumer_filter_id": "f",
                "provider_filter_id": "other",
                "l4_params": [{"proto": 6, "port": [443, 443], "approved": True}],
            }
        ]
        self.labels = [{"key": "10.0.0.1", "value": {"Env": "Prod"}}]
        self.inventory_rows = []
        self.calls = []
        self.fail_path = None
        self.keep_deleted = False
        self.on_mutation = None

    @property
    def writes(self):
        return [
            call
            for call in self.calls
            if call[0] in ("delete", "put")
            or call[0] == "post"
            and call[1] != "/inventory/search"
        ]

    def search_static_labels(self, ip):
        return copy.deepcopy([r for r in self.labels if r["key"] == ip])

    def request(self, method, path, *, params=None, body=None):
        self.calls.append((method, path, copy.deepcopy(body), copy.deepcopy(params)))
        if method == "get":
            if path == "/sensors":
                return {"results": copy.deepcopy(self.sensors)}
            if path.startswith("/sensors/"):
                return copy.deepcopy(
                    next(a for a in self.sensors if a["uuid"] == path.split("/")[-1])
                )
            if path == "/filters/inventories":
                return copy.deepcopy(self.filters)
            if path.startswith("/filters/inventories/"):
                return copy.deepcopy(
                    next(f for f in self.filters if f["id"] == path.split("/")[-1])
                )
            if path == "/applications":
                return [{"id": "app", "latest_adm_version": 1}]
            if path == "/applications/app":
                return {"id": "app", "latest_adm_version": 1}
            if path == "/applications/app/absolute_policies":
                return {"results": []}
            if path == "/applications/app/default_policies":
                return {"results": copy.deepcopy(self.rules)}
            if path.startswith("/policies/"):
                return copy.deepcopy(
                    next(p for p in self.rules if p["id"] == path.split("/")[-1])
                )
        if path == "/inventory/search" and method == "post":
            return {"results": copy.deepcopy(self.inventory_rows)}
        if self.on_mutation:
            self.on_mutation()
        if path == self.fail_path:
            raise CswRequestError("Simulated failure")
        if method == "delete":
            if self.keep_deleted:
                return {"dependents": ["still-in-use"]}
            if path.startswith("/sensors/"):
                self.sensors = [
                    a for a in self.sensors if a["uuid"] != path.split("/")[-1]
                ]
            elif path.startswith("/filters/inventories/"):
                self.filters = [
                    f for f in self.filters if f["id"] != path.split("/")[-1]
                ]
            elif path.startswith("/policies/"):
                self.rules = [p for p in self.rules if p["id"] != path.split("/")[-1]]
            elif path == "/si_inventory/tags":
                self.labels = [r for r in self.labels if r["key"] != body["ip"]]
            else:
                raise AssertionError(path)
            return None
        if method == "put" and path.startswith("/filters/inventories/"):
            record = next(f for f in self.filters if f["id"] == path.split("/")[-1])
            record.update({k: v for k, v in body.items() if k != "query"})
            record["short_query"] = body["query"]
            return copy.deepcopy(record)
        if method == "post" and path == "/filters/inventories":
            record = {**body, "id": "restored-f", "short_query": body["query"]}
            self.filters.append(record)
            return copy.deepcopy(record)
        if method == "post" and path == "/si_inventory/tags":
            self.labels.append({"key": body["ip"], "value": body["attributes"]})
            return None
        if method == "post" and path == "/applications/app/policies":
            self.rules.append(
                {**body, "application_id": "app", "id": "restored-p", "l4_params": []}
            )
            return copy.deepcopy(self.rules[-1])
        if path == "/policies/restored-p/l4_params" and method == "post":
            self.rules[-1]["l4_params"].append(
                {
                    "id": "port",
                    "proto": body["proto"],
                    "port": [body["start_port"], body["end_port"]],
                }
            )
            return {"id": "port"}
        if path == "/policies/restored-p/l4_params/port" and method == "put":
            self.rules[-1]["l4_params"][-1].update(body)
            return None
        raise AssertionError((method, path, body, params))


def backup_for(planned):
    backup = new_backup(
        command="prune-agents",
        dashboard=normalize_dashboard("acme"),
        operations=planned["operations"],
    )
    backup.update(
        prune_schema=1, cutoff=200, dashboard_url="https://acme.tetrationcloud.com"
    )
    return backup


@pytest.mark.parametrize(
    "value", [None, True, False, 0, -1, "bad", "nan", "inf", 1788220800000, {}]
)
def test_invalid_contact_never_stale(value):
    assert epoch(value) is None
    assert not stale(agent(checked=value), 200)


def test_cutoff_is_inclusive_and_deleted_agents_are_not_selected():
    assert stale(agent(checked="200"), 200)
    assert not stale(agent(checked=201), 200)
    assert not stale({**agent(), "deleted_at": 150}, 200)
    assert not stale({**agent(), "auto_removed": True}, 200)


def test_shared_filter_narrows_and_does_not_mutate_input():
    query = {"type": "or", "filters": [eq("10.0.0.1"), eq("10.0.0.2")]}
    original = copy.deepcopy(query)
    assert prune_query(query, {"10.0.0.1"}) == (eq("10.0.0.2"), {"10.0.0.1"})
    assert query == original


def test_and_false_branch_is_removed_not_broadened():
    query = {
        "type": "or",
        "filters": [
            {
                "type": "and",
                "filters": [
                    eq("10.0.0.1"),
                    {"type": "eq", "field": "Env", "value": "Prod"},
                ],
            },
            eq("10.0.0.2"),
        ],
    }
    assert prune_query(query, {"10.0.0.1"})[0] == eq("10.0.0.2")
    assert prune_query(eq("10.0.0.1"), {"10.0.0.1"})[0] is None


@pytest.mark.parametrize(
    "query",
    [
        {"type": "not", "filter": eq("10.0.0.1")},
        eq("10.0.0.0/24"),
        {"type": "eq", "field": "Env", "value": "Prod"},
        {**eq("10.0.0.1"), "unexpected": True},
        {"type": "and", "filters": []},
    ],
)
def test_unsupported_or_broad_filters_are_not_rewritten(query):
    assert prune_query(query, {"10.0.0.1"}) == (query, set())


def test_membership_and_ipv6():
    query = {"type": "in", "field": "ip", "values": ["2001:db8::1/128", "2001:db8::2"]}
    assert prune_query(query, {"2001:db8::1"})[0]["values"] == ["2001:db8::2"]


def test_ip_reuse_and_observation_protect_related_objects():
    assert safe_ips([agent(), agent("b", 300)], 200, set()) == {}
    assert safe_ips([agent()], 200, {"10.0.0.1"}) == {}
    assert safe_ips([agent(), agent("b", None)], 200, set()) == {}


def test_complete_plan_relationships_and_delete_order():
    backend = Backend()
    result = plan(PruneApi(backend), 200)
    assert [op["kind"] for op in result["operations"]] == [
        "policy",
        "filter",
        "label",
        "agent",
    ]
    assert "consumer filter f" in result["operations"][0]["relation"]
    assert all(op["agents"] == ["a"] for op in result["operations"])
    assert result["operations"][-1]["dependencies"] == [
        "policy:p",
        "filter:f",
        "label:10.0.0.1",
    ]
    assert backend.writes == []


def test_shared_policy_is_preserved_via_filter():
    result = plan(PruneApi(Backend(shared=True)), 200)
    policy, filter_op = result["operations"][:2]
    assert policy["action"] == "acknowledge"
    assert filter_op["action"] == "update"
    assert filter_op["after"]["query"] == eq("10.0.0.2")


def test_observed_inventory_is_reported_not_deleted():
    backend = Backend()
    backend.inventory_rows = [{"ip": "10.0.0.1"}]
    result = plan(PruneApi(backend), 200)
    assert [op["kind"] for op in result["operations"]] == ["agent"]
    assert any("Inventory entry" in reason for reason in result["skipped"])


def test_backup_exists_before_first_mutation(tmp_path):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    path = tmp_path / "backup.json"

    def assert_backup():
        saved = json.loads(path.read_text())
        assert saved["operations"]
        assert any(op["status"] == "attempting" for op in saved["operations"])

    backend.on_mutation = assert_backup
    counts = apply_plan(api, backup, path, lambda op: True, lambda message: None)
    assert counts == {"deleted": 4, "updated": 0, "failed": 0, "skipped": 0}
    assert backend.sensors == [agent("b", 300, "10.0.0.2")]


@pytest.mark.parametrize("declined", ["agent:a", "filter:f", "policy:p"])
def test_declines_protect_related_dependencies(tmp_path, declined):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    apply_plan(
        api,
        backup,
        tmp_path / "backup.json",
        lambda op: op["key"] != declined,
        lambda message: None,
    )
    assert all(call[1] != "/sensors/a" for call in backend.writes)
    assert all(call[1] != "/policies/p" for call in backend.writes)
    assert all(call[1] != "/filters/inventories/f" for call in backend.writes)


def test_failed_policy_blocks_dependents_but_independent_label_continues(tmp_path):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    backend.fail_path = "/policies/p"
    counts = apply_plan(
        api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None
    )
    assert counts["failed"] == 1
    assert counts["deleted"] == 1
    assert counts["skipped"] == 2
    assert backup["operations"][0]["status"] == "uncertain"


def test_recheck_agent_contact_prevents_every_write(tmp_path):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    backend.sensors[0]["last_config_fetch_at"] = 500
    counts = apply_plan(
        api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None
    )
    assert counts["failed"] > 0
    assert backend.writes == []


def test_changed_filter_is_not_overwritten(tmp_path):
    backend = Backend(shared=True)
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    backend.filters[0]["short_query"] = eq("10.0.0.99")
    apply_plan(api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None)
    assert all(call[1] != "/filters/inventories/f" for call in backend.writes)


def test_backup_failure_prevents_writes(tmp_path, monkeypatch):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))

    def fail(*args):
        raise BackupError("disk full")

    monkeypatch.setattr(execution_module, "write_backup", fail)
    with pytest.raises(BackupError):
        apply_plan(
            api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None
        )
    assert backend.writes == []


def test_2xx_dependency_response_is_not_counted_as_deletion(tmp_path):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    backend.keep_deleted = True
    counts = apply_plan(
        api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None
    )
    assert counts["deleted"] == 0
    assert counts["failed"] == 2


@pytest.mark.parametrize("shared", [False, True])
def test_recovery_preserves_policy_services_and_remaps_filters(tmp_path, shared):
    backend = Backend(shared=shared)
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    original = copy.deepcopy(backend.filters[0]["short_query"])
    source = tmp_path / "backup.json"
    apply_plan(api, backup, source, lambda op: True, lambda msg: None)
    journal = {"operations": []}
    counts = recover(
        api,
        backup,
        source,
        journal,
        tmp_path / "recovery.json",
        lambda op: True,
        lambda msg: None,
    )
    assert counts["failed"] == 0
    assert counts["skipped"] == 1  # agent is never recreated
    assert backend.filters[0]["short_query"] == original
    assert backend.labels[0]["value"] == {"Env": "Prod"}
    if not shared:
        assert backend.rules[0]["consumer_filter_id"] == "restored-f"
        assert backend.rules[0]["l4_params"][0]["port"] == [443, 443]
        assert backend.rules[0]["l4_params"][0]["approved"] is True
    before = len(backend.writes)
    recover(
        api,
        backup,
        source,
        {"operations": []},
        tmp_path / "again.json",
        lambda op: True,
        lambda msg: None,
    )
    assert len(backend.writes) == before


def test_recovery_refuses_changed_filter_and_uncertain_operations(tmp_path):
    backend = Backend(shared=True)
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    source = tmp_path / "backup.json"
    apply_plan(api, backup, source, lambda op: True, lambda msg: None)
    backend.filters[0]["short_query"] = eq("10.0.0.99")
    for op in backup["operations"]:
        if op["kind"] == "label":
            op["status"] = "uncertain"
    before = len(backend.writes)
    counts = recover(
        api,
        backup,
        source,
        {"operations": []},
        tmp_path / "recovery.json",
        lambda op: True,
        lambda msg: None,
    )
    assert counts["failed"] == 2
    assert len(backend.writes) == before


def test_help_documents_destructive_limits_without_auth():
    result = CliRunner().invoke(cli, ["prune-agents", "--help"])
    assert result.exit_code == 0
    for term in (
        "DESTRUCTIVE",
        "no true rollback",
        "--noconfirm",
        "--lastdate",
        "30 days",
        "last_config_fetch_at",
        "review",
        "--dry-run",
        "--rollback",
    ):
        assert term in result.output


@pytest.mark.parametrize("flags", [[], ["--dry-run"], ["--noconfirm"]])
def test_cli_defaults_to_read_only(monkeypatch, flags):
    backend = Backend()
    monkeypatch.setattr(command_module, "api_for", lambda app: backend)
    result = CliRunner().invoke(
        cli, ["-d", "acme", "prune-agents", "--lastdate", "200", *flags]
    )
    assert result.exit_code == 0, result.output
    assert "Dry run: no objects changed" in result.output
    assert "consumer filter f" in result.output
    assert backend.writes == []


def test_cli_apply_prompts_for_every_element_and_defaults_no(monkeypatch, tmp_path):
    backend = Backend()
    monkeypatch.setattr(command_module, "api_for", lambda app: backend)
    result = CliRunner().invoke(
        cli,
        [
            "-d",
            "acme",
            "prune-agents",
            "--lastdate",
            "200",
            "--apply",
            "--backup-dir",
            str(tmp_path),
        ],
        input="\n" * 4,
    )
    assert result.exit_code == 0, result.output
    assert result.output.count("Proceed with this element?") == 4
    assert "skipped=4" in result.output
    assert backend.writes == []
    assert list(tmp_path.glob("prune-agents-*.json"))


def test_cli_noconfirm_does_not_require_terminal(monkeypatch, tmp_path):
    backend = Backend()
    monkeypatch.setattr(command_module, "api_for", lambda app: backend)
    monkeypatch.setattr(
        command_module.interaction, "is_interactive_terminal", lambda: False
    )
    result = CliRunner().invoke(
        cli,
        [
            "-d",
            "acme",
            "prune-agents",
            "--lastdate",
            "200",
            "--noconfirm",
            "--apply",
            "--backup-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 0, result.output
    assert "DESTRUCTIVE" in result.output
    assert "Proceed with this element?" not in result.output
    assert "deleted=4" in result.output


@pytest.mark.parametrize("date", ["0", "-1", "9999999999", "1788220800000", "nan"])
def test_cli_rejects_bad_epoch_before_connecting(monkeypatch, date):
    monkeypatch.setattr(
        command_module, "api_for", lambda app: pytest.fail("must not connect")
    )
    result = CliRunner().invoke(cli, ["-d", "acme", "prune-agents", "--lastdate", date])
    assert result.exit_code == 2


def test_listing_paginates_and_detects_repeated_offset():
    backend = Backend()
    responses = iter(
        [{"results": [agent()], "offset": "next"}, {"results": [agent("b")]}]
    )
    backend.request = lambda *args, **kwargs: next(responses)
    assert len(PruneApi(backend).agents()) == 2
    backend.request = lambda *args, **kwargs: {"results": [agent()], "offset": "same"}
    with pytest.raises(CswRequestError, match="Repeated"):
        PruneApi(backend).agents()


@pytest.mark.parametrize(
    "payload", [{}, {"results": [None]}, {"results": [agent(), agent()]}]
)
def test_bad_agent_listing_fails_closed(payload):
    backend = Backend()
    backend.request = lambda *args, **kwargs: payload
    with pytest.raises((CswRequestError, ValueError)):
        PruneApi(backend).agents()


def test_changed_exclusive_filter_prevents_policy_deletion(tmp_path):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    backend.filters[0]["short_query"] = eq("10.0.0.2")
    apply_plan(api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None)
    assert all(call[1] != "/policies/p" for call in backend.writes)
    assert all(call[1] != "/sensors/a" for call in backend.writes)


def test_new_policy_dependency_blocks_filter_update(tmp_path):
    backend = Backend(shared=True)
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    backend.rules.append({**copy.deepcopy(backend.rules[0]), "id": "new-policy"})
    apply_plan(api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None)
    assert all(call[1] != "/filters/inventories/f" for call in backend.writes)


def test_live_ip_reuse_after_preview_prevents_related_changes(tmp_path):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    backend.sensors.append(agent("new", 300, "10.0.0.1"))
    apply_plan(api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None)
    assert backend.writes == []


def test_no_stale_agents_does_not_query_other_resources():
    backend = Backend()
    backend.sensors = [agent(checked=300)]
    assert plan(PruneApi(backend), 200)["operations"] == []
    assert [call[1] for call in backend.calls] == ["/sensors"]


def test_primary_filter_is_not_changed():
    backend = Backend()
    backend.filters[0]["primary"] = True
    planned = plan(PruneApi(backend), 200)
    assert all(op["kind"] not in ("policy", "filter") for op in planned["operations"])
    assert any("primary/public" in reason for reason in planned["skipped"])


def test_inventory_pagination_rechecks_all_pages():
    backend = Backend()
    calls = []
    results = iter(
        [
            {"results": [{"ip": "10.0.0.1"}], "offset": {"next": 2}},
            {"results": [{"ip": "10.0.0.2"}]},
        ]
    )

    def request(method, path, **kwargs):
        calls.append(copy.deepcopy(kwargs))
        return next(results)

    backend.request = request
    assert len(PruneApi(backend).inventory()) == 2
    assert calls[1]["body"]["offset"] == {"next": 2}


def test_incomplete_inventory_aborts_discovery():
    backend = Backend()
    backend.inventory_rows = [{}]
    with pytest.raises(CswRequestError, match="missing IP"):
        plan(PruneApi(backend), 200)
    assert backend.writes == []


def test_prune_disables_sdk_mutation_retries_only_temporarily():
    from types import SimpleNamespace

    backend = Backend()
    backend.client = SimpleNamespace(retries=3)
    seen = []
    backend.on_mutation = lambda: seen.append(backend.client.retries)
    PruneApi(backend).mutate("delete", "/sensors/a")
    assert seen == [1]
    assert backend.client.retries == 3
    backend.fail_path = "/sensors/b"
    with pytest.raises(CswRequestError):
        PruneApi(backend).mutate("delete", "/sensors/b")
    assert backend.client.retries == 3


def test_journal_failure_after_a_mutation_stops_all_further_writes(
    tmp_path, monkeypatch
):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))

    def write(path, value):
        if any(op["status"] == "applied" for op in value["operations"]):
            raise BackupError("disk full after API success")
        write_backup(path, value)

    monkeypatch.setattr(execution_module, "write_backup", write)
    with pytest.raises(BackupError):
        apply_plan(
            api, backup, tmp_path / "backup.json", lambda op: True, lambda msg: None
        )
    assert len(backend.writes) == 1
    saved = json.loads((tmp_path / "backup.json").read_text())
    assert saved["operations"][0]["status"] == "attempting"


def test_interrupted_recovery_is_never_retried_automatically(tmp_path):
    backend = Backend()
    api = PruneApi(backend)
    backup = backup_for(plan(api, 200))
    for op in backup["operations"]:
        op.update(status="applied", recovering=True)
    counts = recover(
        api,
        backup,
        tmp_path / "backup.json",
        {"operations": []},
        tmp_path / "recovery.json",
        lambda op: True,
        lambda msg: None,
    )
    assert counts["failed"] == 4
    assert backend.writes == []


@pytest.mark.parametrize("tamper", ["id", "kind", "action", "agents"])
def test_malformed_backup_rejected_before_recovery(tamper):
    backup = backup_for(plan(PruneApi(Backend()), 200))
    backup["operations"][0][tamper] = "invalid"
    with pytest.raises(ValueError):
        validate_recovery(backup)


def test_cli_default_cutoff_is_thirty_days(monkeypatch):
    from datetime import UTC, datetime, timedelta

    backend = Backend()
    monkeypatch.setattr(command_module, "api_for", lambda app: backend)
    seen = []

    def capture(api, cutoff):
        seen.append(cutoff)
        return {"agents": [], "operations": [], "skipped": []}

    monkeypatch.setattr(command_module, "plan", capture)
    lower = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    result = CliRunner().invoke(cli, ["-d", "acme", "prune-agents"])
    upper = (datetime.now(UTC) - timedelta(days=30)).timestamp()
    assert result.exit_code == 0
    assert lower <= seen[0] <= upper


def test_cli_noconfirm_without_dashboard_does_not_prompt(monkeypatch):
    monkeypatch.setattr(
        command_module.interaction, "is_interactive_terminal", lambda: False
    )
    result = CliRunner().invoke(cli, ["prune-agents", "--noconfirm"])
    assert result.exit_code == 2
    assert "CSW dashboard:" not in result.output


def test_cli_failed_run_returns_nonzero_with_journal(monkeypatch, tmp_path):
    backend = Backend()
    backend.fail_path = "/policies/p"
    monkeypatch.setattr(command_module, "api_for", lambda app: backend)
    result = CliRunner().invoke(
        cli,
        [
            "-d",
            "acme",
            "prune-agents",
            "--lastdate",
            "200",
            "--apply",
            "--noconfirm",
            "--backup-dir",
            str(tmp_path),
        ],
    )
    assert result.exit_code == 1
    assert "FAILED/UNCERTAIN policy:p" in result.output
    assert "Summary:" in result.output
    journal = json.loads(next(tmp_path.glob("prune-agents-*.json")).read_text())
    assert journal["summary"]["failed"] == 1


def test_cli_recovery_checks_dashboard_before_mutation(monkeypatch, tmp_path):
    backend = Backend()
    backup = backup_for(plan(PruneApi(backend), 200))
    backup["dashboard_url"] = "https://another.example.org"
    source = tmp_path / "backup.json"
    write_backup(source, backup)
    monkeypatch.setattr(command_module, "api_for", lambda app: backend)
    result = CliRunner().invoke(
        cli,
        [
            "-d",
            "acme",
            "prune-agents",
            "--apply",
            "--rollback",
            str(source),
            "--noconfirm",
        ],
    )
    assert result.exit_code == 1
    assert "does not match" in result.output
    assert backend.writes == []
