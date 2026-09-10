"""Checked OpenAPI operations used only by agent pruning.

Keep release-specific response validation here, rather than changing the API
behavior of existing commands. Unknown response shapes fail closed.
"""

from __future__ import annotations

import json
from typing import Any
from urllib.parse import quote

from csw_tools.csw_api import CswApi, CswRequestError


def identifier(value: Any) -> str:
    if not isinstance(value, str) or not value or value in (".", ".."):
        raise ValueError("Missing or invalid CSW object identifier")
    return quote(value, safe="")


def objects(payload: Any, path: str) -> list[dict[str, Any]]:
    if not isinstance(payload, list) or any(not isinstance(x, dict) for x in payload):
        raise CswRequestError(f"Unexpected object list from {path}; pruning stopped")
    return payload


class PruneApi:
    def __init__(self, api: CswApi, page_size: int = 500):
        self.api = api
        self.page_size = page_size

    def listing(self, path: str, *, paginated: bool = False, **params):
        if paginated:
            params["limit"] = self.page_size
        found = []
        offsets = set()
        while True:
            payload = self.api.request("get", path, params=params or None)
            if isinstance(payload, list):
                found.extend(objects(payload, path))
                if paginated and len(payload) >= self.page_size:
                    raise CswRequestError(
                        f"{path} returned a full page without a pagination envelope"
                    )
                return found
            if not isinstance(payload, dict) or "results" not in payload:
                raise CswRequestError(f"Unexpected response from {path}")
            found.extend(objects(payload["results"], path))
            offset = payload.get("offset")
            if offset is None or offset == "":
                return found
            key = json.dumps(offset, sort_keys=True)
            if key in offsets:
                raise CswRequestError(f"Repeated pagination offset from {path}")
            offsets.add(key)
            params["offset"] = offset

    def get(self, path: str):
        result = self.api.request("get", path)
        if not isinstance(result, dict):
            raise CswRequestError(f"Unexpected object from {path}")
        return result

    def inventory(self):
        body = {"filter": {}, "dimensions": ["ip"], "limit": self.page_size}
        records = []
        offsets = set()
        while True:
            result = self.api.request("post", "/inventory/search", body=body)
            if not isinstance(result, dict):
                raise CswRequestError("Unexpected inventory response")
            page = objects(result.get("results"), "/inventory/search")
            if any(not isinstance(row.get("ip"), str) for row in page):
                raise CswRequestError(
                    "Inventory row missing IP; cannot prove ownership"
                )
            records.extend(page)
            offset = result.get("offset")
            if offset in (None, "", {}):
                return records
            key = json.dumps(offset, sort_keys=True)
            if key in offsets:
                raise CswRequestError("Repeated inventory offset")
            offsets.add(key)
            body["offset"] = offset

    def agents(self):
        records = self.listing("/sensors", paginated=True)
        seen = set()
        for record in records:
            uuid = record.get("uuid")
            identifier(uuid)
            if uuid in seen:
                raise CswRequestError(
                    "Duplicate agent UUID in listing; pruning stopped"
                )
            seen.add(uuid)
        return records

    def policies(self):
        """Only edit the latest working v* version; never publish or enforce."""
        found = []
        for workspace in self.listing("/applications"):
            app_id = identifier(workspace.get("id"))
            version = workspace.get("latest_adm_version")
            if type(version) is not int or version < 0:
                raise CswRequestError(
                    f"Workspace {app_id} has no usable latest_adm_version"
                )
            for rank in ("absolute_policies", "default_policies"):
                for policy in self.listing(
                    f"/applications/{app_id}/{rank}",
                    paginated=True,
                    version=f"v{version}",
                ):
                    if policy.get("version") != f"v{version}":
                        raise CswRequestError(
                            "Policy version differs from requested version"
                        )
                    if policy.get("application_id") != workspace["id"]:
                        raise CswRequestError(
                            "Policy belongs to an unexpected workspace"
                        )
                    identifier(policy.get("id"))
                    found.append(policy)
        return found

    def mutate(self, method: str, path: str, body=None):
        # tetpyclient may retry DELETE/PUT by default. A pruning mutation must
        # have only one attempt: its outcome could be uncertain after a timeout.
        client = getattr(self.api, "client", None)
        retries = getattr(client, "retries", None)
        if retries is not None:
            client.retries = 1
        try:
            result = self.api.request(method, path, body=body)
        finally:
            if retries is not None:
                client.retries = retries
        if isinstance(result, dict) and (
            result.get("success") is False or result.get("errors")
        ):
            raise CswRequestError(f"CSW rejected {method.upper()} {path}")
        return result
