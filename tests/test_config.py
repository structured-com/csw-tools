from pathlib import Path

import pytest

import csw_tools.config as config_module
from csw_tools.config import ConfigError, load_config, resolve_setting


def test_missing_default_config_is_empty(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    default_path = tmp_path / "missing" / "config.toml"
    monkeypatch.setattr(config_module, "default_config_path", lambda: default_path)

    config = load_config()

    assert config.common.keyring_service_name is None
    assert config.common.dashboard is None
    assert config.common.dashboard_verify_tls is None
    assert config.common.output_dir is None
    assert config.common.log_cli_output is None
    assert config.source_path is None
    assert config.for_command("prune-agents") == {}


def test_missing_explicit_config_is_an_error(tmp_path: Path) -> None:
    config_path = tmp_path / "missing.toml"

    with pytest.raises(ConfigError, match="does not exist"):
        load_config(config_path, explicit=True)


def test_loads_common_and_command_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        """
[common]
keyring_service_name = " team-csw-tools "
dashboard = " HTTPS://My-Company.TetrationCloud.com/ "
dashboard_verify_tls = false
output_dir = " ~/csw-tools-history "
log_cli_output = false

[prune-agents]
dry_run = true

[prune-policy]
workspace = "Epic"

[sync-collection-rules]
""".strip(),
        encoding="utf-8",
    )

    config = load_config(config_path, explicit=True)

    assert config.source_path == config_path
    assert config.common.keyring_service_name == "team-csw-tools"
    assert config.common.dashboard is not None
    assert config.common.dashboard.name == "my-company"
    assert config.common.dashboard.fqdn == "my-company.tetrationcloud.com"
    assert config.common.dashboard.url == "https://my-company.tetrationcloud.com"
    assert config.common.dashboard_verify_tls is False
    assert config.common.output_dir == Path.home() / "csw-tools-history"
    assert config.common.log_cli_output is False
    assert config.for_command("prune-agents") == {"dry_run": True}
    assert config.as_click_default_map()["prune-policy"] == {"workspace": "Epic"}


def test_loads_non_saas_fqdn_from_configuration(tmp_path: Path) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        '[common]\ndashboard = "CSW.EXAMPLE.ORG"\n',
        encoding="utf-8",
    )

    config = load_config(config_path, explicit=True)

    assert config.common.dashboard is not None
    assert config.common.dashboard.name == "https://csw.example.org"
    assert config.common.dashboard.fqdn == "csw.example.org"
    assert config.common.dashboard.url == "https://csw.example.org"


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("[unknown]\nvalue = true\n", "Unknown configuration section"),
        ('common = "not-a-table"\n', "must be a TOML table"),
        ("[common]\nunknown = true\n", "Unknown key"),
        ("[common]\nkeyring_service_name = 42\n", "must be a string"),
        ('[common]\nkeyring_service_name = "   "\n', "cannot be empty"),
        ("[common]\ndashboard = 42\n", "dashboard must be a string"),
        ('[common]\ndashboard = "bad_host.example.com"\n', "Invalid on-premises"),
        (
            '[common]\ndashboard_verify_tls = "false"\n',
            "dashboard_verify_tls must be a boolean",
        ),
        ("[common]\noutput_dir = 42\n", "output_dir must be a string"),
        ('[common]\noutput_dir = "   "\n', "output_dir cannot be empty"),
        (
            '[common]\nlog_cli_output = "true"\n',
            "log_cli_output must be a boolean",
        ),
        ("[common\n", "Invalid TOML"),
    ],
)
def test_rejects_invalid_configuration(
    tmp_path: Path, contents: str, message: str
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(contents, encoding="utf-8")

    with pytest.raises(ConfigError, match=message):
        load_config(config_path, explicit=True)


def test_resolution_precedence_stops_before_prompt() -> None:
    def unexpected_prompt(_label: str) -> str:
        pytest.fail("prompt must not be called when a value is already available")

    assert (
        resolve_setting(
            name="setting",
            cli_value="cli",
            config_value="config",
            default="default",
            required=True,
            prompt=unexpected_prompt,
        )
        == "cli"
    )
    assert (
        resolve_setting(
            name="setting",
            config_value="config",
            default="default",
            required=True,
            prompt=unexpected_prompt,
        )
        == "config"
    )
    assert (
        resolve_setting(
            name="setting",
            default="default",
            required=True,
            prompt=unexpected_prompt,
        )
        == "default"
    )


def test_required_value_prompts_when_unresolved() -> None:
    prompts: list[str] = []

    def prompt(label: str) -> str:
        prompts.append(label)
        return "interactive"

    result = resolve_setting(
        name="workspace",
        required=True,
        prompt=prompt,
    )

    assert result == "interactive"
    assert prompts == ["workspace"]


def test_optional_missing_value_remains_none() -> None:
    assert resolve_setting(name="optional") is None


@pytest.mark.parametrize(
    "section_name",
    [
        "clean-stale-labels",
        "convert-labels",
        "create-scopes",
        "prune-agents",
    ],
)
def test_rejects_legacy_command_backup_directory(
    tmp_path: Path, section_name: str
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        f'[{section_name}]\nbackup_dir = "old-backups"\n',
        encoding="utf-8",
    )

    with pytest.raises(ConfigError, match=r"moved to \[common\]\.output_dir"):
        load_config(config_path, explicit=True)
