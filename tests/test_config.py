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
    assert config.for_command("prune-agents") == {"dry_run": True}
    assert config.as_click_default_map()["prune-policy"] == {"workspace": "Epic"}


@pytest.mark.parametrize(
    ("contents", "message"),
    [
        ("[unknown]\nvalue = true\n", "Unknown configuration section"),
        ('common = "not-a-table"\n', "must be a TOML table"),
        ("[common]\nunknown = true\n", "Unknown key"),
        ("[common]\nkeyring_service_name = 42\n", "must be a string"),
        ('[common]\nkeyring_service_name = "   "\n', "cannot be empty"),
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
            input_is_tty=True,
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
            input_is_tty=True,
            prompt=unexpected_prompt,
        )
        == "config"
    )
    assert (
        resolve_setting(
            name="setting",
            default="default",
            required=True,
            input_is_tty=True,
            prompt=unexpected_prompt,
        )
        == "default"
    )


def test_required_value_prompts_only_when_interactive() -> None:
    prompts: list[str] = []

    def prompt(label: str) -> str:
        prompts.append(label)
        return "interactive"

    result = resolve_setting(
        name="workspace",
        required=True,
        allow_input=True,
        input_is_tty=True,
        prompt=prompt,
    )

    assert result == "interactive"
    assert prompts == ["workspace"]


def test_required_value_fails_when_input_is_disabled() -> None:
    with pytest.raises(ConfigError, match="--no-input"):
        resolve_setting(name="workspace", required=True, allow_input=False)


def test_required_value_fails_without_a_tty() -> None:
    with pytest.raises(ConfigError, match="interactive input is unavailable"):
        resolve_setting(
            name="workspace",
            required=True,
            allow_input=True,
            input_is_tty=False,
        )


def test_optional_missing_value_remains_none() -> None:
    assert resolve_setting(name="optional") is None
