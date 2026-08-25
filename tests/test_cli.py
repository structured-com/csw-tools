from pathlib import Path

import click
import pytest
from click.testing import CliRunner

import csw_tools.cli as cli_module
import csw_tools.config as config_module
from csw_tools.cli import cli


@pytest.fixture(autouse=True)
def isolate_default_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        config_module,
        "default_config_path",
        lambda: tmp_path / "user-config" / "config.toml",
    )


def test_help_lists_all_commands_and_global_options() -> None:
    result = CliRunner().invoke(cli, ["--help"])

    assert result.exit_code == 0
    assert "--config" in result.output
    assert "--keyring-service-name" in result.output
    assert "--no-input" in result.output
    for command_name in (
        "init",
        "prune-agents",
        "prune-policy",
        "sync-collection-rules",
    ):
        assert command_name in result.output


def test_version_comes_from_package_metadata() -> None:
    result = CliRunner().invoke(cli, ["--version"])

    assert result.exit_code == 0
    assert "0.1.0" in result.output


@pytest.mark.parametrize(
    "command_name",
    ["init", "prune-agents", "prune-policy", "sync-collection-rules"],
)
def test_placeholder_commands_report_pending_and_fail(command_name: str) -> None:
    result = CliRunner().invoke(cli, [command_name])

    assert result.exit_code == 1
    assert "not implemented yet" in result.output


def test_init_does_not_write_to_keyring(monkeypatch: pytest.MonkeyPatch) -> None:
    def unexpected_write(*_args: str) -> None:
        pytest.fail("the placeholder init command must not write to keyring")

    monkeypatch.setattr(cli_module.KeyringStore, "set_password", unexpected_write)

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 1
    assert "no changes were made" in result.output


def test_explicit_missing_config_is_a_click_error(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"

    result = CliRunner().invoke(cli, ["--config", str(config_path), "init"])

    assert result.exit_code == 1
    assert "Configuration file does not exist" in result.output


def test_invalid_config_is_a_click_error(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[unknown]\n", encoding="utf-8")

    result = CliRunner().invoke(cli, ["--config", str(config_path), "init"])

    assert result.exit_code == 1
    assert "Unknown configuration section" in result.output


def test_empty_cli_keyring_service_name_is_an_error() -> None:
    result = CliRunner().invoke(cli, ["--keyring-service-name", "   ", "init"])

    assert result.exit_code == 1
    assert "keyring service name cannot be empty" in result.output


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
    assert from_config.output.strip() == "from-config"
    assert from_cli.exit_code == 0
    assert from_cli.output.strip() == "from-cli"
    assert from_backend.exit_code == 0
    assert from_backend.output.strip() == "from-backend"


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
    arguments: list[str] = []

    if config_service_name is not None:
        config_path = tmp_path / "config.toml"
        config_path.write_text(
            f'[common]\nkeyring_service_name = "{config_service_name}"\n',
            encoding="utf-8",
        )
        arguments.extend(("--config", str(config_path)))

    if cli_service_name is not None:
        arguments.extend(("--keyring-service-name", cli_service_name))

    arguments.append("init")
    result = CliRunner().invoke(cli, arguments)

    assert result.exit_code == 1
    assert captured_service_names == [expected]
