import json
import random
from copy import deepcopy
from importlib import import_module
from ipaddress import ip_address, ip_network
from types import SimpleNamespace

import pytest
from click.testing import CliRunner

from csw_tools.change_control import BackupError
from csw_tools.cli import cli
from csw_tools.commands.sync_collection_rules.compliance import (
    check_compliance,
    effective_include_ranges,
    parse_collection_rules,
    parse_labels_csv,
    plan_additions,
)
from csw_tools.csw_api import CswApi

command_module = import_module("csw_tools.commands.sync_collection_rules.command")
apply_module = import_module("csw_tools.commands.sync_collection_rules.apply")


def catch_alls():
    return [
        {"subnet": "0.0.0.0/0", "action": "EXCLUDE"},
        {"subnet": "::/0", "action": "EXCLUDE"},
    ]


class CollectionClient:
    def __init__(self):
        self.csv = "IP,Environment\n10.9.9.9,Prod\n192.0.2.10,Test\n"
        self.rules = [{"subnet": "10.0.0.0/8", "action": "INCLUDE"}]
        self.calls = []
        self.status = 200
        self.post_calls = []
        self.post_status = 200
        self.post_content = ...
        self.post_error = None
        self.before_post = lambda: None

    def get(self, path, **kwargs):
        self.calls.append((path, kwargs))
        if path.startswith("/assets/cmdb/download/"):
            content = self.csv.encode("utf-8")
        elif path.startswith("/collection_rules/"):
            content = json.dumps(self.rules).encode("utf-8")
        else:
            pytest.fail(f"Unexpected API path: {path}")
        return SimpleNamespace(status_code=self.status, content=content)

    def post(self, path, **kwargs):
        self.before_post()
        self.post_calls.append((path, kwargs))
        if self.post_error:
            raise self.post_error
        payload = json.loads(kwargs["json_body"])
        if self.post_status == 200:
            self.rules = payload
        content = (
            json.dumps(payload).encode()
            if self.post_content is ...
            else self.post_content
        )
        return SimpleNamespace(status_code=self.post_status, content=content)


@pytest.fixture
def client(monkeypatch):
    client = CollectionClient()
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
        parse_labels_csv(f"IP\n{label}\n"),
        parse_collection_rules(
            [{"subnet": subnet, "action": "INCLUDE"} for subnet in includes]
        ),
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


def test_first_matching_exclude_blocks_later_include():
    rules = parse_collection_rules(
        [
            {"subnet": "0.0.0.0/0", "action": "EXCLUDE"},
            {"subnet": "10.9.9.9/8", "action": "INCLUDE"},
            {"subnet": "::/0", "action": "EXCLUDE"},
        ]
    )
    findings = check_compliance(parse_labels_csv("IP\n10.9.9.9\n"), rules)
    assert [finding.issue for finding in findings] == ["No INCLUDE coverage"]


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
        parse_collection_rules(rules)


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
    assert "first matching rule wins" in result.output
    assert "Apply blocked" in result.output
    assert "Prepend INCLUDE" in result.output
    assert "would prepend 2 INCLUDE" in result.output
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
def test_apply_from_cli_or_config(client, tmp_path, via_config):
    client.rules += catch_alls()
    options = ()
    globals = ("--output-dir", str(tmp_path))
    if via_config:
        config = tmp_path / "config.toml"
        config.write_text("[sync-collection-rules]\napply = true\n")
        globals += ("--config", str(config))
    else:
        options = ("--apply",)
    result = invoke(*options, globals=globals)
    assert result.exit_code == 0, result.output
    assert len(client.post_calls) == 1
    assert "Applied and verified 1 INCLUDE addition" in result.output


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
        "Check (and fix) Collection Rules against defined IP sources, "
        "such as user labels.",
        "This module ensures that any IPs/subnets defined in manual user labels are "
        "populated correctly in Collection Rules.",
        "Flow-learnt inventory items excluded by Collection Rules will not exist in "
        "inventory. Therefore, inventory search and policies may not work correctly. "
        "This module helps ensure Collection Rules compliance.",
        "This is most important when non-RFC1918 IPs (public IPs, tunnel IPs) are "
        "needed for scopes and enforcement policy.",
        "When applying changes, INCLUDE statements will be added at the beginning of "
        "the Collection Rules list (no other changes to rules will be made).",
        "Before applying, the original last two rules must be EXCLUDE 0.0.0.0/0 and "
        "EXCLUDE ::/0, in either order. This is best practice, so any incorrect rules "
        "must be manually corrected first.",
        "The backend API requires both a VRF name (for Collection Rules) and a root "
        "app scope name (for user labels). These are likely the same as your dashboard "
        "name, so that will be attempted by default.",
    ]
    for description in descriptions:
        assert description in output
    assert "--apply" in output and "--dry-run" in output
    assert "--rollback" not in output
    assert "--root-app-scope-name" in output and "--vrf-name" in output
    assert "DEV/TESTING" not in output
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


@pytest.mark.parametrize(
    ("label", "ordered", "issue"),
    [
        ("10.0.0.0/24", [("10.0.0.0/24", "INCLUDE"), ("10.0.0.0/25", "EXCLUDE")], None),
        (
            "10.0.0.0/24",
            [("10.0.0.0/25", "EXCLUDE"), ("10.0.0.0/24", "INCLUDE")],
            "Partial INCLUDE coverage",
        ),
        (
            "10.0.0.1",
            [("10.0.0.0/8", "EXCLUDE"), ("10.0.0.1/32", "INCLUDE")],
            "No INCLUDE coverage",
        ),
        (
            "10.0.0.0/24",
            [
                ("10.0.0.0/26", "INCLUDE"),
                ("10.0.0.0/25", "EXCLUDE"),
                ("10.0.0.0/24", "INCLUDE"),
            ],
            "Partial INCLUDE coverage",
        ),
        (
            "10.0.0.128/25",
            [("10.0.0.0/25", "EXCLUDE"), ("10.0.0.0/24", "INCLUDE")],
            None,
        ),
        (
            "::/0",
            [("2001:db8::/32", "INCLUDE"), ("::/0", "EXCLUDE")],
            "Partial INCLUDE coverage",
        ),
        (
            "2001:db8::/64",
            [("2001:db8::/65", "EXCLUDE"), ("2001:db8::/64", "INCLUDE")],
            "Partial INCLUDE coverage",
        ),
        ("::/0", [("0.0.0.0/0", "EXCLUDE"), ("::/0", "INCLUDE")], None),
        ("255.255.255.255", [("255.255.255.255/32", "INCLUDE")], None),
        ("ffff:ffff:ffff:ffff:ffff:ffff:ffff:ffff", [("::/0", "INCLUDE")], None),
    ],
)
def test_effective_ordered_coverage(label, ordered, issue):
    rules = parse_collection_rules(
        [{"subnet": subnet, "action": action} for subnet, action in ordered]
    )
    findings = check_compliance(parse_labels_csv(f"IP\n{label}\n"), rules)
    assert [finding.issue for finding in findings] == ([] if issue is None else [issue])


def test_interval_sweep_matches_first_match_reference():
    """Compare overlapping rules with a small independent address-based oracle."""

    generator = random.Random(157)
    first = int(ip_address("192.0.2.0"))
    for _ in range(30):
        payload = [
            {
                "subnet": str(
                    ip_network(
                        (first + generator.randrange(256), generator.randrange(24, 33)),
                        strict=False,
                    )
                ),
                "action": generator.choice(["INCLUDE", "EXCLUDE"]),
            }
            for _ in range(30)
        ]
        rules = parse_collection_rules(payload)
        ranges = effective_include_ranges(rules)[4]
        for value in range(first, first + 256):
            expected = next(
                (
                    rule.action == "INCLUDE"
                    for rule in rules
                    if ip_address(value) in rule.network
                ),
                False,
            )
            assert any(start <= value <= end for start, end in ranges) == expected


def read_history(directory):
    (current_path,) = directory.glob("*-current.json")
    (uploaded_path,) = directory.glob("*-uploaded.json")
    journal_path = directory / current_path.name.replace("-current.json", ".json")
    return (
        json.loads(current_path.read_text()),
        json.loads(uploaded_path.read_text()),
        json.loads(journal_path.read_text()),
    )


@pytest.mark.parametrize("reverse_catch_alls", [False, True])
def test_apply_preserves_payload_and_history_and_is_idempotent(
    client, tmp_path, reverse_catch_alls
):
    client.csv = (
        "IP\n10.9.9.9/8\n10.0.0.0/8\n10.0.0.1\n"
        "192.0.2.8/25\n192.0.2.128/25\n2001:0db8::1\n"
    )
    client.rules = [
        {"subnet": "10.0.0.0/8", "action": "EXCLUDE", "note": {"keep": True}},
        {"subnet": " 10.9.9.9/8 ", "action": "INCLUDE"},
        {"subnet": "10.0.0.0/8", "action": "INCLUDE"},
    ] + catch_alls()[:: (-1 if reverse_catch_alls else 1)]
    original = deepcopy(client.rules)
    additions = [
        {"subnet": subnet, "action": "INCLUDE"}
        for subnet in [
            "10.0.0.0/8",
            "10.0.0.1/32",
            "192.0.2.0/25",
            "192.0.2.128/25",
            "2001:db8::1/128",
        ]
    ]

    def check_before_post():
        current, uploaded, journal = read_history(tmp_path)
        assert current == original
        assert uploaded == additions + original
        assert journal["operations"][0]["status"] == "attempted"
        assert journal["operations"][0]["attempted"] is True

    client.before_post = check_before_post
    globals = ("--output-dir", str(tmp_path), "--no-log-cli-output")
    result = invoke("--apply", "--vrf-name", "VRF/Prod?", globals=globals)
    assert result.exit_code == 0, result.output
    assert len(client.post_calls) == 1
    path, kwargs = client.post_calls[0]
    assert path == "/collection_rules/VRF%2FProd%3F"
    assert json.loads(kwargs["json_body"]) == additions + original
    current, uploaded, journal = read_history(tmp_path)
    assert current == original
    assert uploaded == additions + original
    assert journal["command"] == "sync-collection-rules"
    assert journal["dashboard"] == "my-company"
    assert journal["format_version"] == 1
    assert journal["root_app_scope_name"] == "my-company"
    assert journal["vrf_name"] == "VRF/Prod?"
    assert journal["operations"][0]["status"] == "verified"
    assert journal["operations"][0]["added_rules"] == 5
    base = journal["current_payload"].removesuffix("-current.json")
    assert journal["uploaded_payload"] == f"{base}-uploaded.json"
    assert (tmp_path / f"{base}.json").exists()
    assert "Applied and verified 5" in result.output
    assert len(list(tmp_path.iterdir())) == 3
    second = invoke("--apply", "--vrf-name", "VRF/Prod?", globals=globals)
    assert second.exit_code == 0, second.output
    assert "No additions needed" in second.output
    assert len(client.post_calls) == 1
    assert len(list(tmp_path.iterdir())) == 3


@pytest.mark.parametrize(
    "tail",
    [
        [],
        catch_alls()[:1],
        catch_alls()[1:],
        catch_alls() + [{"subnet": "192.0.2.0/24", "action": "INCLUDE"}],
        [catch_alls()[0], {"subnet": "::/0", "action": "INCLUDE"}],
        [{"subnet": "0.0.0.0/0", "action": "INCLUDE"}, catch_alls()[1]],
        [catch_alls()[0], catch_alls()[0]],
        [catch_alls()[1], catch_alls()[1]],
    ],
)
@pytest.mark.parametrize("csv", ["IP\n", "IP\n10.0.0.1\n", "IP\n192.0.2.1\n"])
def test_invalid_original_tail_blocks_apply_even_without_additions(
    client, tmp_path, tail, csv
):
    client.csv = csv
    client.rules += tail
    globals = ("--output-dir", str(tmp_path), "--no-log-cli-output")
    result = invoke("--apply", globals=globals)
    assert result.exit_code == 1
    assert "Correct the rules manually in the CSW GUI" in result.output
    assert not client.post_calls
    assert not list(tmp_path.iterdir())
    dry_run = invoke("--dry-run", globals=globals)
    assert dry_run.exit_code == 0, dry_run.output
    assert "Apply blocked" in dry_run.output
    assert not client.post_calls
    assert not list(tmp_path.iterdir())


@pytest.mark.parametrize("csv", ["IP\n", "IP\n10.0.0.1\n"])
def test_no_op_apply_has_no_post_or_history(client, tmp_path, csv):
    client.csv = csv
    client.rules += catch_alls()
    result = invoke("--apply", globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 0, result.output
    assert not client.post_calls
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.parametrize("stage", ["current", "uploaded", "journal"])
def test_history_write_failure_prevents_post(client, monkeypatch, tmp_path, stage):
    client.rules += catch_alls()
    write = apply_module.write_backup

    def fail_write(path, payload):
        kind = (
            "current"
            if path.stem.endswith("-current")
            else "uploaded"
            if path.stem.endswith("-uploaded")
            else "journal"
        )
        if kind == stage:
            raise BackupError(f"Could not write backup: {path}")
        write(path, payload)

    monkeypatch.setattr(apply_module, "write_backup", fail_write)
    result = invoke("--apply", globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 1
    assert "Could not write backup" in result.output
    assert not client.post_calls


@pytest.mark.parametrize(
    "failure",
    [
        "http",
        "transport",
        "json",
        "empty",
        "shape",
        "rule",
        "order",
        "action",
        "subnet",
    ],
)
def test_post_failures_preserve_history_and_journal_unverified(
    client, tmp_path, failure
):
    client.rules += catch_alls()
    original = deepcopy(client.rules)
    uploaded = [{"subnet": "192.0.2.10/32", "action": "INCLUDE"}] + original
    if failure == "http":
        client.post_status = 403
        client.post_content = b"secret response must not appear"
    elif failure == "transport":
        client.post_error = RuntimeError("secret transport details must not appear")
    elif failure == "json":
        client.post_content = b"secret invalid JSON must not appear"
    elif failure == "empty":
        client.post_content = b""
    elif failure == "shape":
        client.post_content = b'{"results": []}'
    elif failure == "rule":
        client.post_content = b"[{}]"
    else:
        changed = deepcopy(uploaded)
        if failure == "order":
            changed.reverse()
        elif failure == "action":
            changed[0]["action"] = "EXCLUDE"
        else:
            changed[0]["subnet"] = "192.0.2.11/32"
        client.post_content = json.dumps(changed).encode()
    result = invoke("--apply", globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 1, result.output
    assert len(client.post_calls) == 1
    assert "POST outcome is unverified" in result.output
    assert "Applied and verified" not in result.output
    current, saved_uploaded, journal = read_history(tmp_path)
    assert current == original
    assert saved_uploaded == uploaded
    assert journal["operations"][0]["status"] == "unverified"
    assert "secret" not in result.output
    assert "secret" not in json.dumps(journal)


@pytest.mark.parametrize("post_fails", [False, True])
def test_journal_write_failure_after_post_is_not_reported_as_success(
    client, monkeypatch, tmp_path, post_fails
):
    client.rules += catch_alls()
    if post_fails:
        client.post_status = 500
    write = apply_module.write_backup

    def fail_after_post(path, payload):
        if client.post_calls:
            raise BackupError(f"Could not write backup: {path}")
        write(path, payload)

    monkeypatch.setattr(apply_module, "write_backup", fail_after_post)
    result = invoke("--apply", globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 1, result.output
    assert len(client.post_calls) == 1
    assert "Could not write backup" in result.output
    assert "Applied and verified" not in result.output
    assert "journal still records an attempted POST" in " ".join(result.output.split())
    assert read_history(tmp_path)[2]["operations"][0]["status"] == "attempted"


def test_unwritable_output_directory_prevents_post(client, tmp_path):
    client.rules += catch_alls()
    blocker = tmp_path / "file"
    blocker.write_text("not a directory")
    result = invoke(
        "--apply", globals=("--output-dir", str(blocker), "--no-log-cli-output")
    )
    assert result.exit_code == 2
    assert "is a file" in result.output
    assert not client.post_calls


def test_response_allows_equivalent_network_notation(client, tmp_path):
    client.csv = "IP\n2001:db8::1\n"
    client.rules = catch_alls()
    response = [{"subnet": "2001:0db8:0:0:0:0:0:1/128", "action": "INCLUDE"}]
    client.post_content = json.dumps(response + catch_alls()).encode()
    result = invoke("--apply", globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 0, result.output


def test_addition_planning_keeps_full_networks_and_csv_order():
    entries = parse_labels_csv("IP\n10.0.0.128/25\n10.0.0.9/24\n10.0.0.9/32\n")
    findings = check_compliance(
        entries,
        parse_collection_rules([{"subnet": "10.0.0.0/26", "action": "INCLUDE"}]),
    )
    assert plan_additions(findings) == [
        {"subnet": "10.0.0.128/25", "action": "INCLUDE"},
        {"subnet": "10.0.0.0/24", "action": "INCLUDE"},
    ]


@pytest.mark.parametrize(
    "acknowledgement", [{"success": True}, {"success": True, "message": "updated"}]
)
def test_apply_accepts_success_acknowledgement(client, tmp_path, acknowledgement):
    client.rules += catch_alls()
    original = deepcopy(client.rules)
    client.post_content = json.dumps(acknowledgement).encode()
    globals = ("--output-dir", str(tmp_path))
    result = invoke("--apply", globals=globals)
    assert result.exit_code == 0, result.output
    assert "Applied and verified 1 INCLUDE addition" in result.output
    assert "unverified" not in result.output
    assert "HTTP diagnostics" not in result.output
    assert len(client.post_calls) == 1
    assert len(client.calls) == 2
    current, uploaded, journal = read_history(tmp_path)
    assert current == original
    assert uploaded == [{"subnet": "192.0.2.10/32", "action": "INCLUDE"}] + original
    assert journal["operations"][0]["status"] == "verified"
    assert "response_diagnostics" not in journal["operations"][0]
    second = invoke("--apply", globals=globals)
    assert second.exit_code == 0, second.output
    assert "No additions needed" in second.output
    assert len(client.post_calls) == 1


@pytest.mark.parametrize(
    "response",
    [
        {},
        {"success": False},
        {"success": "true"},
        {"success": 1},
        {"success": 0},
        {"success": None},
        {"status": "success"},
        True,
        None,
        1,
        "success",
    ],
)
def test_apply_rejects_false_missing_or_malformed_success(client, tmp_path, response):
    client.rules += catch_alls()
    client.post_content = json.dumps(response).encode()
    result = invoke("--apply", globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 1, result.output
    assert len(client.post_calls) == 1
    assert "Applied and verified" not in result.output
    assert "HTTP diagnostics" not in result.output
    operation = read_history(tmp_path)[2]["operations"][0]
    assert operation["status"] == "unverified"
    assert "response_diagnostics" not in operation


@pytest.mark.parametrize("status", [400, 403, 500])
def test_success_acknowledgement_cannot_override_http_failure(client, tmp_path, status):
    client.rules += catch_alls()
    client.post_status = status
    client.post_content = b'{"success":true}'
    result = invoke("--apply", globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 1, result.output
    assert f"returned status {status}" in result.output
    assert "Applied and verified" not in result.output
    assert len(client.post_calls) == 1
    assert read_history(tmp_path)[2]["operations"][0]["status"] == "unverified"


def test_get_still_requires_ordered_rules_list(client, tmp_path):
    client.rules = {"success": True}
    result = invoke(globals=("--output-dir", str(tmp_path)))
    assert result.exit_code == 1
    assert "Collection Rules response must be a list of rule objects" in result.output
    assert "HTTP diagnostics" not in result.output
    assert not client.post_calls
    assert not list(tmp_path.glob("*.json"))
