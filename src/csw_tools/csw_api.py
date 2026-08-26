"""Shared construction of authenticated Cisco Secure Workload API clients."""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from typing import Any

from tetpyclient import RestClient

from csw_tools.config_defaults import (
    CSW_API_KEY_USERNAME,
    CSW_API_SECRET_USERNAME,
)
from csw_tools.context import AppContext
from csw_tools.keyring_store import KeyringStoreError


class CswApiError(RuntimeError):
    """Raised when an authenticated CSW API client cannot be created safely."""


class CswRequestError(CswApiError):
    """Raised when a CSW API request fails or returns an invalid response."""


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


class CswApi:
    """Small, checked facade around the tetpyclient response interface."""

    def __init__(self, client: RestClient) -> None:
        self.client = client

    def request(
        self,
        method: str,
        path: str,
        *,
        params: Mapping[str, object] | None = None,
        body: object | None = None,
    ) -> Any:
        """Issue a request and return decoded JSON, if any."""

        request_method = getattr(self.client, method.lower())
        kwargs: dict[str, object] = {}
        if params is not None:
            kwargs["params"] = dict(params)
        if body is not None:
            kwargs["json_body"] = json.dumps(body)

        try:
            response = request_method(path, **kwargs)
        except Exception as exc:
            raise CswRequestError(f"CSW API {method.upper()} {path} failed") from exc

        status = getattr(response, "status_code", None)
        if not isinstance(status, int) or not 200 <= status < 300:
            status_text = status if status is not None else "unknown"
            raise CswRequestError(
                f"CSW API {method.upper()} {path} returned status {status_text}"
            )

        content = getattr(response, "content", b"")
        if content in (b"", "", None):
            return None
        try:
            if isinstance(content, bytes):
                content = content.decode("utf-8")
            return json.loads(content)
        except (UnicodeDecodeError, json.JSONDecodeError, TypeError) as exc:
            raise CswRequestError(
                f"CSW API {method.upper()} {path} returned invalid JSON"
            ) from exc

    def inventory(
        self,
        *,
        dimensions: list[str] | None = None,
        scope: str | None = None,
        page_size: int = 500,
    ) -> Iterator[dict[str, object]]:
        """Yield all current inventory records using CSW offset pagination."""

        request_body: dict[str, object] = {"filter": {}, "limit": page_size}
        if dimensions:
            request_body["dimensions"] = dimensions
        if scope:
            request_body["scopeName"] = scope

        seen_offsets: set[str] = set()
        while True:
            payload = self.request("post", "/inventory/search", body=request_body)
            if not isinstance(payload, dict) or not isinstance(
                payload.get("results"), list
            ):
                raise CswRequestError(
                    "CSW API POST /inventory/search returned an unexpected payload"
                )
            for item in payload["results"]:
                if isinstance(item, dict):
                    yield item

            offset = payload.get("offset")
            if offset in (None, "", {}):
                return
            offset_key = json.dumps(offset, sort_keys=True)
            if offset_key in seen_offsets:
                raise CswRequestError("CSW inventory pagination repeated an offset")
            seen_offsets.add(offset_key)
            request_body["offset"] = offset

    def get_static_label(self, ip: str) -> dict[str, object] | None:
        payload = self.request("get", "/si_inventory/tags", params={"ip": ip})
        if payload in (None, {}):
            return None
        if not isinstance(payload, dict):
            raise CswRequestError("Static label lookup returned an unexpected payload")
        attributes = payload.get("attributes", payload)
        if not isinstance(attributes, dict):
            raise CswRequestError("Static label attributes are not a JSON object")
        return attributes

    def set_static_label(self, ip: str, attributes: Mapping[str, object]) -> None:
        self.request(
            "post",
            "/si_inventory/tags",
            body={"ip": ip, "attributes": dict(attributes)},
        )

    def delete_static_label(self, ip: str) -> None:
        self.request("delete", "/si_inventory/tags", body={"ip": ip})

    def search_static_labels(self, ip_range: str) -> list[dict[str, object]]:
        payload = self.request(
            "get", "/si_inventory/tags/search", params={"ip": ip_range}
        )
        if not isinstance(payload, list):
            raise CswRequestError("Static label search returned an unexpected payload")
        return [item for item in payload if isinstance(item, dict)]

    def get_scopes(self) -> list[dict[str, object]]:
        payload = self.request("get", "/app_scopes")
        if not isinstance(payload, list):
            raise CswRequestError("Scope listing returned an unexpected payload")
        return [item for item in payload if isinstance(item, dict)]

    def create_scope(self, payload: Mapping[str, object]) -> dict[str, object]:
        result = self.request("post", "/app_scopes", body=dict(payload))
        if not isinstance(result, dict):
            raise CswRequestError("Scope creation returned an unexpected payload")
        return result

    def delete_scope(self, scope_id: str) -> None:
        self.request("delete", f"/app_scopes/{scope_id}")
