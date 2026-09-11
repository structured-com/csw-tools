from importlib import import_module, resources
from pathlib import Path

import pytest
from click.testing import CliRunner

from csw_tools.cli import cli
from csw_tools.config_defaults import CONFIG_EXAMPLE_FILENAME

init_module = import_module("csw_tools.commands.init.command")


def _example_config_contents() -> bytes:
    return resources.files("csw_tools").joinpath(CONFIG_EXAMPLE_FILENAME).read_bytes()


def test_example_config_documents_common_output_defaults() -> None:
    contents = _example_config_contents().decode("utf-8")

    assert '# output_dir = "csw-tools-outputs"' in contents
    assert "# log_cli_output = true" in contents
    assert "backup_dir" not in contents


def test_init_creates_default_config_and_parent_directories(tmp_path: Path) -> None:
    config_path = tmp_path / "user-config" / "config.toml"

    result = CliRunner().invoke(cli, ["init"], input="\n")

    assert result.exit_code == 0
    assert config_path.read_bytes() == _example_config_contents()
    assert f"Created configuration: '{config_path}'" in result.output
    assert "csw-tools configure-credentials" in result.output
    assert "Open configuration directory now? [y/N]" in result.output


def test_init_honors_explicit_config_path(tmp_path: Path) -> None:
    config_path = tmp_path / "custom" / "settings.toml"

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="\n",
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
        input="\n\n",
    )

    assert result.exit_code == 0
    assert config_path.read_bytes() == original_contents
    assert "Overwrite it? [y/N]" in result.output
    assert f"Retained existing configuration: '{config_path}'" in result.output
    assert "Open configuration directory now? [y/N]" in result.output


def test_init_uses_valid_existing_common_output_settings(tmp_path: Path) -> None:
    output_dir = tmp_path / "configured-output"
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[common]\noutput_dir = "{output_dir}"\nlog_cli_output = true\n',
        encoding="utf-8",
    )

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="\n\n",
    )

    assert result.exit_code == 0
    assert list(output_dir.glob("csw-tools-init-*.log"))


def test_init_replaces_existing_config_when_confirmed(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("old contents\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="y\n\n",
    )

    assert result.exit_code == 0
    assert config_path.read_bytes() == _example_config_contents()
    assert f"Replaced configuration: '{config_path}'" in result.output
    assert "Open configuration directory now? [y/N]" in result.output


def test_init_can_replace_malformed_config(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text("[invalid\n", encoding="utf-8")

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="y\n\n",
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
    assert "Open configuration directory now?" not in result.output


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
    assert "Open configuration directory now?" not in result.output


def test_init_opens_config_parent_directory_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "custom" / "config.toml"
    opened: list[Path] = []
    monkeypatch.setattr(init_module, "_open_directory", opened.append)

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert opened == [config_path.parent]


@pytest.mark.parametrize(
    ("platform_name", "expected_command"),
    [
        ("darwin", "open"),
        ("linux", "xdg-open"),
    ],
)
def test_open_directory_uses_native_unix_launcher(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    platform_name: str,
    expected_command: str,
) -> None:
    calls: list[tuple[list[str], dict[str, object]]] = []
    directory = tmp_path / "config"

    def fake_run(command: list[str], **kwargs: object) -> None:
        calls.append((command, kwargs))

    monkeypatch.setattr(init_module.sys, "platform", platform_name)
    monkeypatch.setattr(init_module.subprocess, "run", fake_run)

    init_module._open_directory(directory)

    assert calls == [
        (
            [expected_command, str(directory)],
            {
                "check": True,
                "stdout": init_module.subprocess.DEVNULL,
                "stderr": init_module.subprocess.DEVNULL,
            },
        )
    ]


def test_open_directory_uses_windows_shell_action(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    directory = tmp_path / "config"
    opened: list[Path] = []
    monkeypatch.setattr(init_module.sys, "platform", "win32")
    monkeypatch.setattr(init_module.os, "startfile", opened.append, raising=False)

    init_module._open_directory(directory)

    assert opened == [directory]


def test_init_warns_without_failing_when_directory_cannot_open(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    config_path = tmp_path / "config.toml"

    def fail_to_open(*_args: object, **_kwargs: object) -> None:
        raise FileNotFoundError("native launcher unavailable")

    monkeypatch.setattr(init_module.sys, "platform", "linux")
    monkeypatch.setattr(init_module.subprocess, "run", fail_to_open)

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "init"],
        input="y\n",
    )

    assert result.exit_code == 0
    assert config_path.read_bytes() == _example_config_contents()
    assert "Warning: Could not open configuration directory" in result.output
    assert str(config_path.parent) in result.output
