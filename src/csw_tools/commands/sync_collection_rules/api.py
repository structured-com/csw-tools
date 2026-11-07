"""Read-only, scoped API operations for Collection Rules compliance."""

from urllib.parse import quote

from csw_tools.commands.sync_collection_rules.compliance import (
    IPNetwork,
    LabelEntry,
    parse_include_networks,
    parse_labels_csv,
)
from csw_tools.csw_api import CswApi


class CollectionRulesApi:
    def __init__(self, api: CswApi) -> None:
        self.api = api

    def manual_labels(self, root_app_scope_name: str) -> list[LabelEntry]:
        path = f"/assets/cmdb/download/{quote(root_app_scope_name, safe='')}"
        return parse_labels_csv(self.api.request_text("get", path))

    def include_networks(self, vrf_name: str) -> list[IPNetwork]:
        path = f"/collection_rules/{quote(vrf_name, safe='')}"
        return parse_include_networks(self.api.request("get", path))
