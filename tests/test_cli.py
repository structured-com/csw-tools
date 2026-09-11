import platform
import tomllib
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import csw_tools.cli as cli_module
import csw_tools.interaction as interaction_module
from csw_tools.cli import cli
from csw_tools.context import AppContext, pass_app_context


def test_help_lists_all_commands_and_global_options() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--keyring-service-name" in result.output
    assert "-d, --dashboard" in result.output
    assert "--dashboard-verify-tls" in result.output
    assert "--no-dashboard-verify-tls" in result.output
    assert "--output-dir" in result.output
    assert "--log-cli-output" in result.output
    assert "--no-log-cli-output" in result.output
    assert "--no-input" not in result.output
    for command_name in (
        "configure-credentials",
        "init",
        "prune-agents",
        "prune-policy",
        "sync-collection-rules",
    ):
        assert command_name in result.output


@pytest.mark.parametrize("args", [[], ["--help"], ["-h"]])
@pytest.mark.parametrize("terminal_width", [40, 80, 120])
def test_command_descriptions_wrap_without_truncation(
    args: list[str], terminal_width: int
) -> None:
    result = CliRunner().invoke(cli, args, terminal_width=terminal_width)

    assert result.exit_code == (2 if not args else 0)
    # Compare command text independently of wrapping, including line breaks
    # after hyphens on narrow terminals. The Usage line's [ARGS]... is valid.
    output = "".join(result.output.split("Commands:", 1)[1].split())
    assert "InspectandreplacetheCSWAPIcredentialpair." in output
    assert "Runthesync-collection-rulesutility(notimplementedyet)." in output
    assert "..." not in output


def test_development_commands_are_marked_in_root_help() -> None:
    result = CliRunner().invoke(cli, ["--help"], terminal_width=120)

    assert result.exit_code == 0
    for command_name in (
        "clean-stale-labels",
        "convert-labels",
        "prune-agents",
        "prune-policy",
        "sync-collection-rules",
    ):
        command = cli.commands[command_name]
        assert command.get_short_help_str().startswith("(DEV/TESTING)")

    for command_name in ("configure-credentials", "create-scopes", "init"):
        command = cli.commands[command_name]
        assert not command.get_short_help_str().startswith("(DEV/TESTING)")


def test_version_comes_from_package_metadata() -> None:
    result = CliRunner().invoke(cli, ["--version"])
    with Path("pyproject.toml").open("rb") as project_file:
        project = tomllib.load(project_file)["project"]

    assert result.exit_code == 0
    assert result.output == (
        f"{project['name']} {project['version']}\n"
        f"{project['description']}\n"
        f"Python: project {Path('.python-version').read_text().strip()} | "
        f"required {project['requires-python']} | running {platform.python_version()}\n"
        f"License: {project['license']}\n"
        f"Homepage: {project['urls']['Homepage']}\n"
    )


def test_version_exits_before_loading_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_config_load(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("version output loaded configuration")

    monkeypatch.setattr(cli_module, "load_config", unexpected_config_load)

    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert "CSW dashboard:" not in result.output


@pytest.mark.parametrize(
    "command_name",
    ["prune-policy", "sync-collection-rules"],
)
def test_placeholder_commands_report_pending_and_fail(command_name: str) -> None:
    result = CliRunner().invoke(cli, ["--dashboard", "my-company", command_name])

    assert result.exit_code == 1
    assert "not implemented yet" in result.output
    assert "CSW Dashboard" in result.output
    assert "Name: my-company" in result.output
    assert "URL: https://my-company.tetrationcloud.com" in result.output


def test_explicit_missing_config_is_a_click_error(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "prune-policy"],
    )

    assert result.exit_code == 1
    assert "Configuration file does not exist" in result.output


def test_invalid_config_is_a_click_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[unknown]\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "prune-policy"],
    )

    assert result.exit_code == 1
    assert "Unknown configuration section" in result.output


def test_empty_cli_keyring_service_name_is_an_error() -> None:
    result = CliRunner().invoke(cli, ["--keyring-service-name", "   ", "init"])

    assert result.exit_code == 1
    assert "keyring service name cannot be empty" in result.output


def test_invalid_cli_dashboard_is_a_click_error() -> None:
    result = CliRunner().invoke(
        cli,
        ["--dashboard", "bad_host.example.com", "prune-policy"],
    )

    assert result.exit_code == 2
    assert "Invalid value for '-d' / '--dashboard'" in result.output
    assert "Invalid on-premises dashboard hostname" in result.output


def test_command_configuration_becomes_click_defaults(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @click.command("prune-policy")
    @click.option("--workspace", default="from-backend")
    def probe_command(workspace: str) -> None:
        click.echo(workspace)

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[prune-policy]\nworkspace = "from-config"\n',
        encoding="utf-8",
    )
    runner = CliRunner()

    from_config = runner.invoke(cli, ["--config", str(config_path), "prune-policy"])
    from_cli = runner.invoke(
        cli,
        [
            "--config",
            str(config_path),
            "prune-policy",
            "--workspace",
            "from-cli",
        ],
    )
    from_backend = runner.invoke(cli, ["prune-policy"])

    assert from_config.exit_code == 0
    assert from_config.stdout.strip() == "from-config"
    assert from_cli.exit_code == 0
    assert from_cli.stdout.strip() == "from-cli"
    assert from_backend.exit_code == 0
    assert from_backend.stdout.strip() == "from-backend"


@pytest.mark.parametrize(
    ("config_service_name", "cli_service_name", "expected"),
    [
        (None, None, "csw-tools"),
        ("from-config", None, "from-config"),
        ("from-config", "from-cli", "from-cli"),
    ],
)
def test_keyring_service_name_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_service_name: str | None,
    cli_service_name: str | None,
    expected: str,
) -> None:
    captured_service_names: list[str] = []

    class FakeKeyringStore:
        def __init__(self, service_name: str) -> None:
            captured_service_names.append(service_name)

    monkeypatch.setattr(cli_module, "KeyringStore", FakeKeyringStore)
    arguments = ["--dashboard", "my-company"]

    if config_service_name is not None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'[common]\nkeyring_service_name = "{config_service_name}"\n',
            encoding="utf-8",
        )
        arguments.extend(("--config", str(config_path)))

    if cli_service_name is not None:
        arguments.extend(("--keyring-service-name", cli_service_name))

    arguments.append("prune-policy")
    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 1
    assert captured_service_names == [f"{expected}:my-company"]


def test_missing_dashboard_prompts_retries_and_prints_normalized_panel() -> None:
    result = CliRunner().invoke(
        cli,
        ["prune-policy"],
        input=("bad_host.example.com\n  CSW.EXAMPLE.ORG  \n"),
    )

    assert result.exit_code == 1
    assert "CSW dashboard:" in result.output
    assert "Invalid on-premises dashboard hostname" in result.output
    assert result.output.count("CSW Dashboard") == 1
    assert "Name: csw.example.org" in result.output
    assert "URL: https://csw.example.org" in result.output
    assert "not implemented yet" in result.output


def test_dashboard_panel_safely_renders_ipv6_origin() -> None:
    result = CliRunner().invoke(
        cli,
        ["--dashboard", "https://[2001:db8::1]", "prune-policy"],
    )

    assert result.exit_code == 1
    assert "Name: 2001:db8::1" in result.output
    assert "URL: https://[2001:db8::1]" in result.output


def test_dashboard_panel_has_blank_line_before_and_after() -> None:
    result = CliRunner().invoke(
        cli,
        ["--dashboard", "my-company", "prune-policy"],
    )

    lines = result.output.splitlines()
    top = next(index for index, line in enumerate(lines) if "CSW Dashboard" in line)
    bottom = next(
        index
        for index, line in enumerate(lines[top + 1 :], start=top + 1)
        if line.startswith("╰")
    )
    assert lines[top - 1] == ""
    assert lines[bottom + 1] == ""


def test_cli_dashboard_overrides_config_dashboard(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[common]\ndashboard = "from-config"\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(config_path),
            "--dashboard",
            "from-cli",
            "prune-policy",
        ],
    )

    assert result.exit_code == 1
    assert "from-cli" in result.output
    assert "from-config" not in result.output
    assert "CSW dashboard:" not in result.output


@pytest.mark.parametrize(
    ("config_value", "cli_option", "expected"),
    [
        (None, None, True),
        (False, None, False),
        (False, "--dashboard-verify-tls", True),
        (True, "--no-dashboard-verify-tls", False),
    ],
)
def test_dashboard_verify_tls_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    config_value: bool | None,
    cli_option: str | None,
    expected: bool,
) -> None:
    @click.command("prune-policy")
    @pass_app_context
    def probe_command(app: AppContext) -> None:
        click.echo(str(app.dashboard_verify_tls))

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    arguments: list[str] = []
    if config_value is not None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f"[common]\ndashboard_verify_tls = {str(config_value).lower()}\n",
            encoding="utf-8",
        )
        arguments.extend(("--config", str(config_path)))
    if cli_option is not None:
        arguments.append(cli_option)
    arguments.append("prune-policy")

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0
    assert result.stdout.strip() == str(expected)


@pytest.mark.parametrize(
    ("use_config", "use_cli", "expected_source"),
    [
        (False, False, "backend"),
        (True, False, "config"),
        (True, True, "cli"),
    ],
)
def test_output_directory_precedence(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    use_config: bool,
    use_cli: bool,
    expected_source: str,
) -> None:
    @click.command("prune-policy")
    @pass_app_context
    def probe_command(app: AppContext) -> None:
        click.echo(str(app.output_dir))

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    config_output = tmp_path / "from-config"
    cli_output = tmp_path / "from-cli"
    arguments: list[str] = ["--no-log-cli-output"]
    if use_config:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'[common]\noutput_dir = "{config_output}"\n',
            encoding="utf-8",
        )
        arguments.extend(("--config", str(config_path)))
    if use_cli:
        arguments.extend(("--output-dir", str(cli_output)))
    arguments.append("prune-policy")

    result = CliRunner().invoke(cli, arguments)

    expected = {
        "backend": Path(cli_module.DEFAULT_OUTPUT_DIRECTORY).resolve(),
        "config": config_output.resolve(),
        "cli": cli_output.resolve(),
    }[expected_source]
    assert result.exit_code == 0
    assert result.stdout.strip() == str(expected)


@pytest.mark.parametrize(
    "command_name",
    [
        "configure-credentials",
        "init",
        "prune-agents",
        "prune-policy",
        "sync-collection-rules",
    ],
)
def test_commands_require_an_interactive_terminal(
    monkeypatch: pytest.MonkeyPatch,
    command_name: str,
) -> None:
    monkeypatch.setattr(
        interaction_module,
        "is_interactive_terminal",
        lambda: False,
    )

    result = CliRunner().invoke(cli, [command_name])

    assert result.exit_code == 1
    assert "requires an interactive terminal" in result.output


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["--version"],
        ["init", "--help"],
        ["configure-credentials", "--help"],
    ],
)
def test_help_and_version_work_without_an_interactive_terminal(
    monkeypatch: pytest.MonkeyPatch,
    arguments: list[str],
) -> None:
    monkeypatch.setattr(
        interaction_module,
        "is_interactive_terminal",
        lambda: False,
    )

    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 0
    assert "CSW dashboard:" not in result.output
