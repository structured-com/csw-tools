"""Shared construction of authenticated Cisco Secure Workload API clients."""

from __future__ import annotations

from tetpyclient import RestClient

from csw_tools.config_defaults import (
    CSW_API_KEY_USERNAME,
    CSW_API_SECRET_USERNAME,
)
from csw_tools.context import AppContext
from csw_tools.keyring_store import KeyringStoreError


class CswApiError(RuntimeError):
    """Raised when an authenticated CSW API client cannot be created safely."""


def _credential_guidance(dashboard_name: str) -> str:
    return (
        f"Run 'csw-tools --dashboard {dashboard_name} configure-credentials' "
        "to configure them."
    )


def create_api_client(app: AppContext) -> RestClient:
    """Create a CSW API client from the active dashboard and keyring entries."""

    dashboard = app.dashboard
    if dashboard is None:
        raise CswApiError("CSW dashboard context has not been activated")

    try:
        keyring = app.keyring
    except RuntimeError as exc:
        raise CswApiError("CSW dashboard context has not been activated") from exc

    try:
        api_key = keyring.get_password(CSW_API_KEY_USERNAME)
        api_secret = keyring.get_password(CSW_API_SECRET_USERNAME)
    except KeyringStoreError as exc:
        raise CswApiError(
            f"Could not read CSW API credentials for dashboard "
            f"'{dashboard.name}'. {_credential_guidance(dashboard.name)}"
        ) from exc

    if not api_key or not api_secret:
        raise CswApiError(
            f"CSW API credentials are missing for dashboard '{dashboard.name}'. "
            f"{_credential_guidance(dashboard.name)}"
        )

    try:
        return RestClient(
            dashboard.url,
            api_key=api_key,
            api_secret=api_secret,
            verify=app.dashboard_verify_tls,
        )
    except UnicodeEncodeError as exc:
        raise CswApiError(
            f"CSW API credentials are invalid for dashboard '{dashboard.name}'. "
            f"{_credential_guidance(dashboard.name)}"
        ) from exc
