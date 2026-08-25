from types import SimpleNamespace

import pytest

import csw_tools.csw_api as csw_api_module
from csw_tools.config_defaults import (
    CSW_API_KEY_USERNAME,
    CSW_API_SECRET_USERNAME,
)
from csw_tools.csw_api import CswApiError, create_api_client
from csw_tools.dashboard import normalize_dashboard
from csw_tools.keyring_store import KeyringStoreError


class FakeKeyringStore:
    def __init__(self, stored: dict[str, str | None]) -> None:
        self.stored = stored
        self.reads: list[str] = []

    def get_password(self, username: str) -> str | None:
        self.reads.append(username)
        return self.stored.get(username)


def _app(
    keyring: object,
    *,
    verify_tls: bool = True,
) -> SimpleNamespace:
    return SimpleNamespace(
        dashboard=normalize_dashboard("my-company"),
        dashboard_verify_tls=verify_tls,
        keyring=keyring,
    )


@pytest.mark.parametrize("verify_tls", [True, False])
def test_create_api_client_passes_credentials_directly_in_memory(
    monkeypatch: pytest.MonkeyPatch,
    verify_tls: bool,
) -> None:
    keyring = FakeKeyringStore(
        {
            CSW_API_KEY_USERNAME: "actual-key",
            CSW_API_SECRET_USERNAME: "actual-secret",
        }
    )
    calls: list[tuple[str, dict[str, object]]] = []
    expected_client = object()

    def fake_rest_client(endpoint: str, **kwargs: object) -> object:
        calls.append((endpoint, kwargs))
        return expected_client

    monkeypatch.setattr(csw_api_module, "RestClient", fake_rest_client)

    client = create_api_client(_app(keyring, verify_tls=verify_tls))

    assert client is expected_client
    assert keyring.reads == [CSW_API_KEY_USERNAME, CSW_API_SECRET_USERNAME]
    assert calls == [
        (
            "https://my-company.tetrationcloud.com",
            {
                "api_key": "actual-key",
                "api_secret": "actual-secret",
                "verify": verify_tls,
            },
        )
    ]


@pytest.mark.parametrize(
    "stored",
    [
        {},
        {CSW_API_KEY_USERNAME: "actual-key"},
        {CSW_API_SECRET_USERNAME: "actual-secret"},
        {CSW_API_KEY_USERNAME: "", CSW_API_SECRET_USERNAME: "actual-secret"},
    ],
)
def test_create_api_client_rejects_missing_credentials_without_constructing(
    monkeypatch: pytest.MonkeyPatch,
    stored: dict[str, str],
) -> None:
    def unexpected_rest_client(*_args: object, **_kwargs: object) -> None:
        pytest.fail("RestClient must not be constructed without both credentials")

    monkeypatch.setattr(csw_api_module, "RestClient", unexpected_rest_client)

    with pytest.raises(CswApiError) as error:
        create_api_client(_app(FakeKeyringStore(stored)))

    assert "credentials are missing" in str(error.value)
    assert "configure-credentials" in str(error.value)
    for value in stored.values():
        if value:
            assert value not in str(error.value)


def test_create_api_client_sanitizes_keyring_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backend_secret = "backend-error-containing-a-secret"

    class FailingKeyringStore:
        def get_password(self, _username: str) -> None:
            raise KeyringStoreError(backend_secret)

    def unexpected_rest_client(*_args: object, **_kwargs: object) -> None:
        pytest.fail("RestClient must not be constructed after a keyring failure")

    monkeypatch.setattr(csw_api_module, "RestClient", unexpected_rest_client)

    with pytest.raises(CswApiError) as error:
        create_api_client(_app(FailingKeyringStore()))

    assert "Could not read CSW API credentials" in str(error.value)
    assert "configure-credentials" in str(error.value)
    assert backend_secret not in str(error.value)


def test_create_api_client_requires_activated_dashboard() -> None:
    app = SimpleNamespace(
        dashboard=None,
        dashboard_verify_tls=True,
        keyring=FakeKeyringStore({}),
    )

    with pytest.raises(CswApiError, match="has not been activated"):
        create_api_client(app)
