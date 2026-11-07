import json
from importlib import import_module
from ipaddress import ip_network
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from csw_tools.cli import cli
from csw_tools.commands.sync_collection_rules.compliance import (
    check_compliance,
    parse_include_networks,
    parse_labels_csv,
)
from csw_tools.csw_api import CswApi

command_module = import_module("csw_tools.commands.sync_collection_rules.command")


class ReadOnlyClient:
    def __init__(self):
        self.csv = "IP,Environment\n10.9.9.9,Prod\n192.0.2.10,Test\n"
        self.rules = [{"subnet": "10.0.0.0/8", "action": "INCLUDE"}]
        self.calls = []
        self.status = 200

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.startswith("/assets/cmdb/download/"):
            content = self.csv.encode("utf-8")
        elif path.startswith("/collection_rules/"):
            content = json.dumps(self.rules).encode("utf-8")
        else:
            pytest.fail(f"Unexpected API path: {path}")
        return SimpleNamespace(status_code=self.status, content=content)


@pytest.fixture
def client(monkeypatch):
    client = ReadOnlyClient()
    monkeypatch.setattr(command_module, "api_for", lambda _app: CswApi(client))
    return client


def invoke(*options, dashboard="my-company", globals=()):
    return CliRunner().invoke(
        cli,
        ["--dashboard", dashboard, *globals, "sync-collection-rules", *options],
        terminal_width=120,
    )


@pytest.mark.parametrize(
    ("label", "includes", "issue"),
    [
        ("10.9.9.9", ["10.0.0.0/8"], None),
        ("10.0.0.0/8", ["10.0.0.0/8"], None),
        ("10.0.0.0/24", ["10.0.0.128/25", "10.0.0.0/25"], None),
        ("10.0.0.0/24", ["10.0.0.0/26", "10.0.0.64/26", "10.0.0.128/25"], None),
        ("10.0.0.0/24", ["10.0.0.0/25"], "Partial INCLUDE coverage"),
        ("10.0.0.0/24", ["10.0.0.128/25"], "Partial INCLUDE coverage"),
        ("10.0.0.0/24", ["10.0.0.64/26"], "Partial INCLUDE coverage"),
        ("10.0.0.0/24", ["10.0.0.0/26", "10.0.0.128/25"], "Partial INCLUDE coverage"),
        ("10.0.0.0/24", ["10.0.1.0/24"], "No INCLUDE coverage"),
        ("10.0.1.0/24", ["10.0.0.0/24"], "No INCLUDE coverage"),
        ("0.0.0.0/0", ["0.0.0.0/0"], None),
        ("255.255.255.255", ["0.0.0.0/0"], None),
        ("2001:db8::1", ["2001:db8::/32"], None),
        ("2001:db8::/64", ["2001:db8::/65", "2001:db8:0:0:8000::/65"], None),
        ("2001:db8::/64", ["2001:db8::/65"], "Partial INCLUDE coverage"),
        ("::/0", ["::/0"], None),
        ("0.0.0.1", ["::/0"], "No INCLUDE coverage"),
        ("::1", ["0.0.0.0/0"], "No INCLUDE coverage"),
        ("192.0.2.10", [], "No INCLUDE coverage"),
    ],
)
def test_coverage(label, includes, issue):
    findings = check_compliance(
        parse_labels_csv(f"IP\n{label}\n"), map(ip_network, includes)
    )
    assert [finding.issue for finding in findings] == ([] if issue is None else [issue])


def test_csv_groups_normalized_networks_and_hosts_preserving_rows():
    entries = parse_labels_csv(
        '\ufeffIP,Name\r\n10.9.9.9/8,"Line one\r\nLine two"\r\n'
        "10.0.0.0/8,Other\r\n\r\n , \r\n"
        "192.0.2.10,Host\r\n192.0.2.10/32,Other\r\n"
        "2001:0DB8:0::1,IPv6\r\n2001:db8::1/128,Other\r\n"
    )
    assert [(entry.display, entry.rows) for entry in entries] == [
        ("10.0.0.0/8", (2, 4)),
        ("192.0.2.10", (7, 8)),
        ("2001:db8::1", (9, 10)),
    ]


@pytest.mark.parametrize("text", ["IP\n", "IP,Name\n\n , \n"])
def test_header_only_or_blank_rows(text):
    assert parse_labels_csv(text) == []


@pytest.mark.parametrize(
    ("text", "error"),
    [
        ("", "first header"),
        ("Name,IP\nfoo,10.0.0.1\n", "first header"),
        ("ip\n10.0.0.1\n", "first header"),
        ("IP,Name\n,Missing\n", "row 2"),
        ("IP\n999.1.1.1\n", "row 2"),
        ("IP\n10.0.0.0/33\n", "row 2"),
        ("IP\n2001:db8::/129\n", "row 2"),
        ("IP\nfe80::1%eth0\n", "row 2"),
        ('IP,Name\n10.0.0.1,"Unterminated\n', "Malformed manual-label CSV"),
        ('IP\n"10.0.0.1"extra\n', "Malformed manual-label CSV"),
    ],
)
def test_bad_label_csv(text, error):
    with pytest.raises(ValueError, match=error):
        parse_labels_csv(text)


def test_rules_only_includes_contribute_and_order_is_ignored():
    includes = parse_include_networks(
        [
            {"subnet": "0.0.0.0/0", "action": "EXCLUDE"},
            {"subnet": "10.9.9.9/8", "action": "INCLUDE"},
            {"subnet": "::/0", "action": "EXCLUDE"},
        ]
    )
    assert includes == [ip_network("10.0.0.0/8")]
    assert check_compliance(parse_labels_csv("IP\n10.9.9.9\n"), includes) == []


@pytest.mark.parametrize(
    "rules",
    [
        None,
        {},
        {"results": []},
        [None],
        [{}],
        [{"action": "include", "subnet": "10.0.0.0/8"}],
        [{"action": "INCLUDE", "subnet": None}],
        [{"action": "INCLUDE", "subnet": 1234}],
        [{"action": "INCLUDE", "subnet": "invalid"}],
        [{"action": "EXCLUDE", "subnet": "invalid"}],
    ],
)
def test_bad_rule_responses(rules):
    with pytest.raises(ValueError, match="Collection Rule"):
        parse_include_networks(rules)


def test_cli_reports_only_findings_and_exits_zero(client, tmp_path):
    client.csv += "192.0.2.10/32,Duplicate\n198.51.100.0/24,Partial\n"
    client.rules.append({"subnet": "198.51.100.0/25", "action": "INCLUDE"})
    output_dir = tmp_path / "outputs"
    result = invoke(globals=("--output-dir", str(output_dir), "--no-log-cli-output"))
    assert result.exit_code == 0, result.output
    assert "192.0.2.10" in result.output
    assert "3, 4" in result.output
    assert "198.51.100.0/24" in result.output
    assert "No INCLUDE coverage" in result.output
    assert "Partial INCLUDE coverage" in result.output
    assert "10.9.9.9" not in result.output
    assert "Checked 3 unique entries | Covered 1 | Need action 2" in result.output
    assert "EXCLUDE rules and rule precedence are not evaluated" in " ".join(
        result.output.split()
    )
    assert client.calls == [
        ("/assets/cmdb/download/my-company", {}),
        ("/collection_rules/my-company", {}),
    ]
    assert not output_dir.exists()


@pytest.mark.parametrize(
    ("dashboard", "name"),
    [
        ("MY-COMPANY", "my-company"),
        ("my-company.tetrationcloud.com", "my-company"),
        ("https://my-company.tetrationcloud.com", "my-company"),
        ("csw.example.org", "csw.example.org"),
        ("https://csw.example.org", "csw.example.org"),
        ("https://csw-local", "csw-local"),
        ("https://192.0.2.10", "192.0.2.10"),
        ("https://[2001:db8::1]", "2001%3Adb8%3A%3A1"),
    ],
)
def test_cli_dashboard_name_defaults(client, dashboard, name):
    result = invoke(dashboard=dashboard)
    assert result.exit_code == 0, result.output
    assert [path for path, _ in client.calls] == [
        f"/assets/cmdb/download/{name}",
        f"/collection_rules/{name}",
    ]


def test_cli_config_precedence_and_independent_defaults(client, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text(
        "[sync-collection-rules]\napply = true\n"
        'root_app_scope_name = "Config Scope"\nvrf_name = "Config VRF"\n'
    )
    result = invoke(
        "--dry-run",
        "--root-app-scope-name",
        "CLI Scope/Prod",
        globals=("--config", str(config)),
    )
    assert result.exit_code == 0, result.output
    assert [path for path, _ in client.calls] == [
        "/assets/cmdb/download/CLI%20Scope%2FProd",
        "/collection_rules/Config%20VRF",
    ]
    client.calls.clear()
    result = invoke("--vrf-name", "Override")
    assert result.exit_code == 0
    assert [path for path, _ in client.calls] == [
        "/assets/cmdb/download/my-company",
        "/collection_rules/Override",
    ]


def test_config_scope_and_cli_vrf(client, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[sync-collection-rules]\nroot_app_scope_name = "Scope"\n')
    result = invoke("--vrf-name", "VRF", globals=("--config", str(config)))
    assert result.exit_code == 0
    assert [path for path, _ in client.calls] == [
        "/assets/cmdb/download/Scope",
        "/collection_rules/VRF",
    ]


def test_names_are_encoded_and_rendered_without_markup(client):
    result = invoke(
        "--root-app-scope-name",
        "[red]Prod[/red]",
        "--vrf-name",
        "VRF?x=#%",
    )
    assert result.exit_code == 0, result.output
    assert "Root app scope: [red]Prod[/red]" in result.output
    assert "VRF: VRF?x=#%" in result.output
    assert [path for path, _ in client.calls] == [
        "/assets/cmdb/download/%5Bred%5DProd%5B%2Fred%5D",
        "/collection_rules/VRF%3Fx%3D%23%25",
    ]


@pytest.mark.parametrize("option", ["--root-app-scope-name", "--vrf-name"])
@pytest.mark.parametrize("value", ["", "   ", ".", ".."])
def test_bad_names_fail_before_api(client, option, value):
    result = invoke(option, value)
    assert result.exit_code == 2
    assert "nonempty scope or VRF name" in result.output
    assert client.calls == []


def test_empty_config_name_rejected(client, tmp_path):
    config = tmp_path / "config.toml"
    config.write_text('[sync-collection-rules]\nvrf_name = ""\n')
    result = invoke(globals=("--config", str(config)))
    assert result.exit_code == 2
    assert client.calls == []


@pytest.mark.parametrize("via_config", [False, True])
def test_apply_is_rejected_before_credentials(monkeypatch, tmp_path, via_config):
    def unexpected_api(_app):
        pytest.fail("--apply must not access credentials or APIs")

    monkeypatch.setattr(command_module, "api_for", unexpected_api)
    if via_config:
        config = tmp_path / "config.toml"
        config.write_text("[sync-collection-rules]\napply = true\n")
        result = invoke(globals=("--config", str(config)))
    else:
        result = invoke("--apply")
    assert result.exit_code == 2
    assert "--apply is not available in phase 1" in result.output


def test_rollback_is_not_an_option(client):
    result = invoke("--rollback", "anything.json")
    assert result.exit_code == 2
    assert "No such option" in result.output and "--rollback" in result.output
    assert client.calls == []


@pytest.mark.parametrize("help_option", ["-h", "--help"])
def test_help_contains_description_without_dashboard_or_credentials(help_option):
    result = CliRunner().invoke(cli, ["sync-collection-rules", help_option])
    assert result.exit_code == 0
    output = " ".join(result.output.split())
    descriptions = [
        "This module insures that any IP/subnets defined in manual user labels are "
        "populated correctly in Collection Rules.",
        "For flow learnt inventory items, if they are excluded by collection rules, "
        "inventory item will not exist. Therefore, inventory search and policies may "
        "not work correctly. This module will help ensure compliance of the "
        "Collection Rules.",
        "This is most important when non-RFC1918 IP's (public IP's, tunnel IP's) are "
        "needed for scopes and enforcement policy.",
    ]
    for description in descriptions:
        assert description in output
    assert "--apply" in output and "--dry-run" in output
    assert "--rollback" not in output
    assert "--root-app-scope-name" in output and "--vrf-name" in output
    assert "CSW dashboard:" not in output


@pytest.mark.parametrize(
    ("csv_text", "rules", "expected"),
    [
        ("IP\n", [], "No manual-label IPs found."),
        ("IP\n10.0.0.1\n", [], "No INCLUDE coverage"),
        (
            "IP\n10.0.0.1\n",
            [{"action": "EXCLUDE", "subnet": "0.0.0.0/0"}],
            "No INCLUDE coverage",
        ),
        (
            "IP\n10.0.0.1\n",
            [{"action": "INCLUDE", "subnet": "0.0.0.0/0"}],
            "All manual-label IPs/subnets have complete INCLUDE coverage.",
        ),
    ],
)
def test_cli_empty_or_complete_results(client, csv_text, rules, expected):
    client.csv, client.rules = csv_text, rules
    result = invoke()
    assert result.exit_code == 0, result.output
    assert expected in result.output
    if expected != "No INCLUDE coverage":
        assert "CSV rows" not in result.output


@pytest.mark.parametrize("failure", ["http", "csv", "rules"])
def test_cli_failure_has_no_completed_summary(client, failure):
    if failure == "http":
        client.status = 403
    elif failure == "csv":
        client.csv = "IP\nbad-ip\n"
    else:
        client.rules = {"unexpected": []}
    result = invoke()
    assert result.exit_code == 1
    assert "Error:" in result.output
    assert "Summary:" not in result.output


def test_report_is_saved_in_shared_transcript(client, tmp_path):
    result = invoke(globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 0
    logs = list(tmp_path.glob("*.log"))
    assert len(logs) == 1
    transcript = logs[0].read_text()
    assert "192.0.2.10" in transcript
    assert "Need action 1" in transcript
    assert not list(tmp_path.glob("*.json"))


def test_long_ipv6_is_fully_visible_in_narrow_report(client, monkeypatch):
    monkeypatch.setenv("COLUMNS", "50")
    address = "2001:db8:abcd:ffff:ffff:ffff:ffff:ffff"
    client.csv = f"IP\n{address}\n"
    client.rules = []
    result = invoke()
    assert result.exit_code == 0
    first_column = "".join(
        line.split("│")[1].strip()
        for line in result.output.splitlines()
        if line.startswith("│")
    )
    assert address in first_column
    assert "…" not in result.output
