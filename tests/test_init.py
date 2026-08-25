from importlib import resources
from pathlib import Path

from click.testing import CliRunner

from csw_tools.cli import cli
from csw_tools.config_defaults import CONFIG_EXAMPLE_FILENAME


def _example_config_contents() -> bytes:
    return resources.files("csw_tools").joinpath(CONFIG_EXAMPLE_FILENAME).read_bytes()


def test_init_creates_default_config_and_parent_directories(tmp_path: Path) -> None:
    config_path = tmp_path / "user-config" / "config.toml"

    result = CliRunner().invoke(cli, ["init"])

    assert result.exit_code == 0
    assert config_path.read_bytes() == _example_config_contents()
    assert f"Created configuration: '{config_path}'" in result.output
    assert "csw-tools configure-credentials" in result.output


def test_init_honors_explicit_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "custom" / "settings.toml"

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
    )

    assert result.exit_code == 0
    assert config_path.read_bytes() == _example_config_contents()
    assert f"'{config_path.resolve()}'" in result.output


def test_init_keeps_existing_config_by_default(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    original_contents = b'[common]\nkeyring_service_name = "custom"\n'
    config_path.write_bytes(original_contents)

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="\n",
    )

    assert result.exit_code == 0
    assert config_path.read_bytes() == original_contents
    assert "Overwrite it? [y/N]" in result.output
    assert f"Retained existing configuration: '{config_path}'" in result.output


def test_init_replaces_existing_config_when_confirmed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("old contents\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert config_path.read_bytes() == _example_config_contents()
    assert f"Replaced configuration: '{config_path}'" in result.output


def test_init_can_replace_malformed_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[invalid\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert "Invalid TOML" not in result.output
    assert config_path.read_bytes() == _example_config_contents()


def test_init_reports_configuration_write_failures(tmp_path: Path) -> None:
    parent_path = tmp_path / "not-a-directory"
    parent_path.write_text("blocking file", encoding="utf-8")
    config_path = parent_path / "config.toml"

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
    )

    assert result.exit_code == 1
    assert f"Could not write configuration file: '{config_path}'" in result.output


def test_init_rejects_existing_non_file_target(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.mkdir()

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
    )

    assert result.exit_code == 2
    assert "File" in result.output
    assert "is a directory" in result.output
