import pytest
from keyring.errors import KeyringError

import csw_tools.keyring_store as keyring_store_module
from csw_tools.config_defaults import (
    CSW_API_KEY_USERNAME,
    CSW_API_SECRET_USERNAME,
    DEFAULT_KEYRING_SERVICE_NAME,
)
from csw_tools.keyring_store import KeyringStore, KeyringStoreError


def test_csw_keyring_defaults_are_stable() -> None:
    assert DEFAULT_KEYRING_SERVICE_NAME == "csw-tools"
    assert CSW_API_KEY_USERNAME == "csw:api_key"
    assert CSW_API_SECRET_USERNAME == "csw:api_secret"


@pytest.mark.parametrize("password", ["stored-password", None])
def test_get_password_uses_service_name_and_username(
    monkeypatch: pytest.MonkeyPatch, password: str | None
) -> None:
    calls: list[tuple[str, str]] = []

    def fake_get_password(service_name: str, username: str) -> str | None:
        calls.append((service_name, username))
        return password

    monkeypatch.setattr(keyring_store_module.keyring, "get_password", fake_get_password)
    store = KeyringStore("custom-service")

    assert store.get_password(CSW_API_KEY_USERNAME) == password
    assert calls == [("custom-service", "csw:api_key")]


def test_set_password_uses_service_name_username_and_password(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, str, str]] = []

    def fake_set_password(service_name: str, username: str, password: str) -> None:
        calls.append((service_name, username, password))

    monkeypatch.setattr(keyring_store_module.keyring, "set_password", fake_set_password)
    store = KeyringStore("custom-service")

    store.set_password(CSW_API_SECRET_USERNAME, "actual-secret")

    assert calls == [("custom-service", "csw:api_secret", "actual-secret")]


@pytest.mark.parametrize("operation", ["get", "set"])
def test_backend_errors_are_wrapped_without_leaking_backend_details(
    monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    secret_backend_detail = "backend-failure-containing-a-secret"

    def fail(*_args: str) -> None:
        raise KeyringError(secret_backend_detail)

    if operation == "get":
        monkeypatch.setattr(keyring_store_module.keyring, "get_password", fail)
    else:
        monkeypatch.setattr(keyring_store_module.keyring, "set_password", fail)

    store = KeyringStore("csw-tools")

    with pytest.raises(KeyringStoreError) as error:
        if operation == "get":
            store.get_password(CSW_API_KEY_USERNAME)
        else:
            store.set_password(CSW_API_KEY_USERNAME, "actual-secret")

    assert secret_backend_detail not in str(error.value)
    assert "actual-secret" not in str(error.value)
