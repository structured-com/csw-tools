from __future__ import annotations

from pathlib import Path

import pytest
from click.testing import CliRunner

import csw_tools.cli as cli_module
from csw_tools.cli import cli
from csw_tools.config_defaults import (
    CSW_API_KEY_USERNAME,
    CSW_API_SECRET_USERNAME,
)
from csw_tools.keyring_store import KeyringStoreError


@pytest.mark.parametrize(
    ("stored", "expected_key_status", "expected_secret_status"),
    [
        ({}, "missing", "missing"),
        ({CSW_API_KEY_USERNAME: "stored-key-value"}, "configured", "missing"),
        (
            {CSW_API_SECRET_USERNAME: "stored-secret-value"},
            "missing",
            "configured",
        ),
        (
            {
                CSW_API_KEY_USERNAME: "stored-key-value",
                CSW_API_SECRET_USERNAME: "stored-secret-value",
            },
            "configured",
            "configured",
        ),
        (
            {
                CSW_API_KEY_USERNAME: "",
                CSW_API_SECRET_USERNAME: "",
            },
            "missing",
            "missing",
        ),
    ],
)
def test_configure_credentials_reports_status_without_values(
    monkeypatch: pytest.MonkeyPatch,
    stored: dict[str, str],
    expected_key_status: str,
    expected_secret_status: str,
) -> None:
    writes: list[tuple[str, str]] = []

    class FakeKeyringStore:
        def __init__(self, service_name: str) -> None:
            self.service_name = service_name

        def get_password(self, username: str) -> str | None:
            return stored.get(username)

        def set_password(self, username: str, password: str) -> None:
            writes.append((username, password))

    monkeypatch.setattr(cli_module, "KeyringStore", FakeKeyringStore)

    result = CliRunner().invoke(
        cli,
        ["--dashboard", "my-company", "configure-credentials"],
        input="\n",
    )

    assert result.exit_code == 0
    assert f"CSW API key: {expected_key_status}" in result.output
    assert f"CSW API secret: {expected_secret_status}" in result.output
    assert "No credentials were changed." in result.output
    for credential_value in stored.values():
        if credential_value:
            assert credential_value not in result.output
    assert writes == []


def test_configure_credentials_collects_pair_before_writing(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "config.toml"
    config_path.write_text(
        (
            '[common]\nkeyring_service_name = "custom-service"\n'
            'dashboard = "my-company"\n'
        ),
        encoding="utf-8",
    )
    writes: list[tuple[str, str]] = []
    service_names: list[str] = []

    class FakeKeyringStore:
        def __init__(self, service_name: str) -> None:
            self.service_name = service_name
            service_names.append(service_name)

        def get_password(self, _username: str) -> None:
            return None

        def set_password(self, username: str, password: str) -> None:
            writes.append((username, password))

    monkeypatch.setattr(cli_module, "KeyringStore", FakeKeyringStore)
    user_input = "y\nactual-key\nactual-key\nactual-secret\nactual-secret\n"

    result = CliRunner().invoke(
        cli,
        ["--config", str(config_path), "configure-credentials"],
        input=user_input,
    )

    assert result.exit_code == 0
    assert service_names == ["custom-service:my-company"]
    assert writes == [
        (CSW_API_KEY_USERNAME, "actual-key"),
        (CSW_API_SECRET_USERNAME, "actual-secret"),
    ]
    assert "actual-key" not in result.output
    assert "actual-secret" not in result.output
    assert "Stored CSW API credentials" in result.output
    assert "custom-service" in result.output


def test_configure_credentials_retries_mismatched_hidden_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    writes: list[tuple[str, str]] = []

    class FakeKeyringStore:
        def __init__(self, service_name: str) -> None:
            self.service_name = service_name

        def get_password(self, _username: str) -> None:
            return None

        def set_password(self, username: str, password: str) -> None:
            writes.append((username, password))

    monkeypatch.setattr(cli_module, "KeyringStore", FakeKeyringStore)
    user_input = (
        "y\nfirst-key\nwrong-key\nfinal-key\nfinal-key\nfinal-secret\nfinal-secret\n"
    )

    result = CliRunner().invoke(
        cli,
        ["--dashboard", "my-company", "configure-credentials"],
        input=user_input,
    )

    assert result.exit_code == 0
    assert "do not match" in result.output
    assert writes == [
        (CSW_API_KEY_USERNAME, "final-key"),
        (CSW_API_SECRET_USERNAME, "final-secret"),
    ]
    for value in ("first-key", "wrong-key", "final-key", "final-secret"):
        assert value not in result.output


def test_configure_credentials_wraps_read_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeKeyringStore:
        def __init__(self, service_name: str) -> None:
            self.service_name = service_name

        def get_password(self, _username: str) -> None:
            raise KeyringStoreError("Could not read credentials safely")

    monkeypatch.setattr(cli_module, "KeyringStore", FakeKeyringStore)

    result = CliRunner().invoke(
        cli,
        ["--dashboard", "my-company", "configure-credentials"],
    )

    assert result.exit_code == 1
    assert "Could not read credentials safely" in result.output


def test_configure_credentials_reports_possibly_incomplete_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class FakeKeyringStore:
        def __init__(self, service_name: str) -> None:
            self.service_name = service_name

        def get_password(self, _username: str) -> None:
            return None

        def set_password(self, username: str, _password: str) -> None:
            if username == CSW_API_SECRET_USERNAME:
                raise KeyringStoreError("Could not store API secret")

    monkeypatch.setattr(cli_module, "KeyringStore", FakeKeyringStore)
    user_input = "y\nwrite-key\nwrite-key\nwrite-secret\nwrite-secret\n"

    result = CliRunner().invoke(
        cli,
        ["--dashboard", "my-company", "configure-credentials"],
        input=user_input,
    )

    assert result.exit_code == 1
    assert "Credential storage may be incomplete" in result.output
    assert "rerun 'csw-tools configure-credentials'" in result.output
    assert "write-key" not in result.output
    assert "write-secret" not in result.output
