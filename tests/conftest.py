from pathlib import Path

import pytest

import csw_tools.cli as cli_module
import csw_tools.config as config_module
import csw_tools.interaction as interaction_module


@pytest.fixture(autouse=True)
def isolate_default_config(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(
        config_module,
        "default_config_path",
        lambda: tmp_path / "user-config" / "config.toml",
    )
    monkeypatch.setattr(
        cli_module,
        "DEFAULT_OUTPUT_DIRECTORY",
        tmp_path / "cli-output",
    )


@pytest.fixture(autouse=True)
def use_interactive_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        interaction_module,
        "is_interactive_terminal",
        lambda: True,
    )
