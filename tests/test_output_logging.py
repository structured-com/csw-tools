from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import click
import pytest
from click.testing import CliRunner

from csw_tools.cli import cli
from csw_tools.output_logging import cli_output_log_path


def test_cli_output_log_path_uses_utc_milliseconds(tmp_path: Path) -> None:
    path = cli_output_log_path(
        tmp_path / "logs",
        "create-scopes",
        now=datetime(2026, 9, 11, 20, 15, 30, 123_999, tzinfo=UTC),
    )

    assert path == (
        tmp_path / "logs" / "csw-tools-create-scopes-20260911T201530123Z.log"
    )


def test_transcript_combines_streams_strips_ansi_and_captures_click_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @click.command("prune-policy")
    def probe_command() -> None:
        click.secho("stdout colored", fg="green")
        click.echo("stderr detail", err=True)
        raise click.ClickException("handled failure")

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    output_dir = tmp_path / "records"

    result = CliRunner().invoke(
        cli,
        ["--output-dir", str(output_dir), "prune-policy"],
        color=True,
    )

    assert result.exit_code == 1
    assert "\x1b[" in result.stdout
    assert "stdout colored" in result.stdout
    assert "stdout colored" not in result.stderr
    assert "stderr detail" in result.stderr
    assert "CLI output log:" in result.stderr
    [log_path] = output_dir.glob("csw-tools-prune-policy-*.log")
    contents = log_path.read_text(encoding="utf-8")
    assert "\x1b[" not in contents
    assert contents.index("stdout colored") < contents.index("stderr detail")
    assert contents.index("stderr detail") < contents.index("Error: handled failure")
    assert f"CLI output log: '{log_path}'" in contents


def test_transcript_captures_prompt_but_not_entered_text(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @click.command("prune-policy")
    def probe_command() -> None:
        click.prompt("Selection")
        click.echo("Selection accepted")

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    output_dir = tmp_path / "records"

    result = CliRunner().invoke(
        cli,
        ["--output-dir", str(output_dir), "prune-policy"],
        input="entered-value\n",
    )

    assert result.exit_code == 0
    [log_path] = output_dir.glob("csw-tools-prune-policy-*.log")
    contents = log_path.read_text(encoding="utf-8")
    assert "Selection:" in contents
    assert "Selection accepted" in contents
    assert "entered-value" not in contents


def test_transcript_captures_subcommand_help_and_option_errors(
    tmp_path: Path,
) -> None:
    help_dir = tmp_path / "help"
    help_result = CliRunner().invoke(
        cli,
        ["--output-dir", str(help_dir), "create-scopes", "--help"],
    )

    assert help_result.exit_code == 0
    [help_log] = help_dir.glob("csw-tools-create-scopes-*.log")
    assert "Usage:" in help_log.read_text(encoding="utf-8")

    error_dir = tmp_path / "error"
    error_result = CliRunner().invoke(
        cli,
        ["--output-dir", str(error_dir), "create-scopes", "--unknown-option"],
    )

    assert error_result.exit_code == 2
    [error_log] = error_dir.glob("csw-tools-create-scopes-*.log")
    assert "No such option '--unknown-option'" in error_log.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "arguments",
    [
        ["--help"],
        ["--version"],
        ["unknown-command"],
    ],
)
def test_root_early_exits_do_not_create_transcripts(
    tmp_path: Path, arguments: list[str]
) -> None:
    output_dir = tmp_path / "records"

    CliRunner().invoke(cli, ["--output-dir", str(output_dir), *arguments])

    assert not output_dir.exists()


def test_configuration_failure_before_resolution_does_not_create_transcript(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[invalid\n", encoding="utf-8")
    output_dir = tmp_path / "records"

    result = CliRunner().invoke(
        cli,
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(output_dir),
            "prune-policy",
        ],
    )

    assert result.exit_code == 1
    assert "Invalid TOML" in result.output
    assert not output_dir.exists()


def test_no_log_cli_output_avoids_creating_output_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @click.command("prune-policy")
    def probe_command() -> None:
        click.echo("completed")

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    output_dir = tmp_path / "records"

    result = CliRunner().invoke(
        cli,
        [
            "--output-dir",
            str(output_dir),
            "--no-log-cli-output",
            "prune-policy",
        ],
    )

    assert result.exit_code == 0
    assert result.stdout == "completed\n"
    assert result.stderr == ""
    assert not output_dir.exists()


def test_cli_rejects_empty_output_directory() -> None:
    result = CliRunner().invoke(
        cli,
        ["--output-dir", "", "prune-policy"],
    )

    assert result.exit_code == 2
    assert "output directory cannot be empty" in result.output


def test_cli_logging_flag_overrides_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @click.command("prune-policy")
    def probe_command() -> None:
        click.echo("completed")

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    output_dir = tmp_path / "from-config"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[common]\noutput_dir = "{output_dir}"\nlog_cli_output = false\n',
        encoding="utf-8",
    )

    disabled = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "prune-policy"],
    )
    enabled = CliRunner().invoke(
        cli,
        [
            "--config",
            str(config_path),
            "--log-cli-output",
            "prune-policy",
        ],
    )
    disabled_by_cli_dir = tmp_path / "disabled-by-cli"
    config_path.write_text(
        "[common]\nlog_cli_output = true\n",
        encoding="utf-8",
    )
    disabled_by_cli = CliRunner().invoke(
        cli,
        [
            "--config",
            str(config_path),
            "--output-dir",
            str(disabled_by_cli_dir),
            "--no-log-cli-output",
            "prune-policy",
        ],
    )

    assert disabled.exit_code == 0
    assert disabled.stderr == ""
    assert enabled.exit_code == 0
    assert "CLI output log:" in enabled.stderr
    assert list(output_dir.glob("csw-tools-prune-policy-*.log"))
    assert disabled_by_cli.exit_code == 0
    assert disabled_by_cli.stderr == ""
    assert not disabled_by_cli_dir.exists()


def test_relative_output_directory_resolves_from_current_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    @click.command("prune-policy")
    @click.pass_obj
    def probe_command(app: object) -> None:
        click.echo(str(app.output_dir))

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    working_directory = tmp_path / "working"
    working_directory.mkdir()
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[common]\noutput_dir = "relative-output"\nlog_cli_output = false\n',
        encoding="utf-8",
    )
    monkeypatch.chdir(working_directory)

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "prune-policy"],
    )

    assert result.exit_code == 0
    assert result.stdout.strip() == str(working_directory / "relative-output")


def test_unwritable_output_location_fails_before_command(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invoked = False

    @click.command("prune-policy")
    def probe_command() -> None:
        nonlocal invoked
        invoked = True

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    blocking_file = tmp_path / "blocking"
    blocking_file.write_text("not a directory", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--output-dir", str(blocking_file / "child"), "prune-policy"],
    )

    assert result.exit_code == 1
    assert "Could not create CLI output log" in result.output
    assert invoked is False


def test_transcript_write_failure_is_a_clear_cli_error(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    invoked = False

    @click.command("prune-policy")
    def probe_command() -> None:
        nonlocal invoked
        invoked = True

    class FailingLog:
        def write(self, _value: str) -> None:
            raise OSError("disk full")

        def flush(self) -> None:
            return None

        def close(self) -> None:
            return None

    monkeypatch.setitem(cli.commands, "prune-policy", probe_command)
    monkeypatch.setattr(Path, "open", lambda *_args, **_kwargs: FailingLog())

    result = CliRunner().invoke(
        cli,
        ["--output-dir", str(tmp_path / "records"), "prune-policy"],
    )

    assert result.exit_code == 1
    assert "Could not write CLI output log" in result.output
    assert invoked is False
