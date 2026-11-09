"""Scoped API operations for Collection Rules compliance and replacement."""

from typing import cast
from urllib.parse import quote

from csw_tools.commands.sync_collection_rules.compliance import (
    LabelEntry,
    parse_collection_rules,
    parse_labels_csv,
)
from csw_tools.csw_api import CswApi


class CollectionRulesApi:
    def __init__(self, api: CswApi) -> None:
        self.api = api

    def manual_labels(self, root_app_scope_name: str) -> list[LabelEntry]:
        path = f"/assets/cmdb/download/{quote(root_app_scope_name, safe='')}"
        return parse_labels_csv(self.api.request_text("get", path))

    def rules(self, vrf_name: str) -> list[dict[str, object]]:
        path = f"/collection_rules/{quote(vrf_name, safe='')}"
        payload = self.api.request("get", path)
        parse_collection_rules(payload)
        return cast(list[dict[str, object]], payload)

    def update(self, vrf_name: str, payload: list[dict[str, object]]) -> None:
        path = f"/collection_rules/{quote(vrf_name, safe='')}"
        response = self.api.request("post", path, body=payload)
        if isinstance(response, dict):
            if response.get("success") is True:
                return
            raise ValueError("Collection Rules POST did not return success=true")
        if not isinstance(response, list):
            raise ValueError(
                'Collection Rules POST response must be {"success": true} '
                "or an ordered list of rule objects"
            )
        if parse_collection_rules(response) != parse_collection_rules(payload):
            raise ValueError(
                "Collection Rules POST response does not match the submitted "
                "ordered subnet/action sequence"
            )
