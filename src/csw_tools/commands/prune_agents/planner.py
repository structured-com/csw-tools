"""Pure, conservative planning of stale-agent relationships."""

from __future__ import annotations

import copy
import ipaddress
import math
from typing import Any

from csw_tools.commands.prune_agents.api import PruneApi, identifier

FILTER_FIELDS = ("name", "app_scope_id", "primary", "public")
POLICY_FIELDS = (
    "application_id",
    "version",
    "rank",
    "policy_action",
    "priority",
    "consumer_filter_id",
    "provider_filter_id",
    "l4_params",
)


def epoch(value: Any) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        number = float(value)
    except ValueError:
        return None
    # Seconds only. Zero means never checked in, not a confirmed stale agent.
    return number if math.isfinite(number) and 0 < number < 10_000_000_000 else None


def last_checkin(agent: dict) -> float | None:
    # last_config_fetch_at is the portable CSW agent contact timestamp. Do not
    # substitute creation time, label update time or software installation time.
    return epoch(agent.get("last_config_fetch_at"))


def stale(agent: dict, cutoff: float) -> bool:
    checked = last_checkin(agent)
    return (
        checked is not None
        and checked <= cutoff
        and not agent.get("deleted_at")
        and not agent.get("auto_removed")
    )


def host_ip(value: Any) -> str | None:
    if not isinstance(value, str):
        return None
    try:
        network = ipaddress.ip_network(value, strict=False)
    except ValueError:
        return None
    if network.num_addresses != 1:
        return None
    address = network.network_address
    if address.is_loopback or address.is_link_local or address.is_unspecified:
        return None
    return str(address)


def agent_ips(agent: dict) -> set[str]:
    interfaces = agent.get("interfaces", [])
    if not isinstance(interfaces, list) or any(
        not isinstance(interface, dict) for interface in interfaces
    ):
        raise ValueError(f"Agent {agent.get('uuid')} has malformed interfaces")
    return {ip for interface in interfaces if (ip := host_ip(interface.get("ip")))}


def safe_ips(
    agents: list[dict], cutoff: float, observed: set[str]
) -> dict[str, list[str]]:
    owners: dict[str, list[dict]] = {}
    for agent in agents:
        for ip in agent_ips(agent):
            owners.setdefault(ip, []).append(agent)
    # An IP is not a globally unique workload identity. Any current observation,
    # unknown-age agent, deleted agent or other owner protects it from cleanup.
    return {
        ip: [owner["uuid"] for owner in records]
        for ip, records in owners.items()
        if ip not in observed and len(records) == 1 and stale(records[0], cutoff)
    }


def prune_query(query: Any, removable: set[str]) -> tuple[Any, set[str]]:
    """Replace explicit stale positive IP terms with FALSE, never drop an AND.

    None is an internal FALSE sentinel, NOT an API query. Empty surviving
    filters are deleted; they are never sent as {} or type=none (match-all).
    Unsupported/negated syntax is left unchanged as a whole.
    """
    if not isinstance(query, dict):
        return query, set()
    kind = query.get("type")
    if kind in ("eq", "in") and query.get("field") == "ip":
        expected = {"type", "field", "value" if kind == "eq" else "values"}
        if set(query) != expected:
            return query, set()
        values = [query["value"]] if kind == "eq" else query["values"]
        if not isinstance(values, list) or not values:
            return query, set()
        removed = {ip for value in values if (ip := host_ip(value)) in removable}
        if not removed:
            return query, set()
        kept = [value for value in values if host_ip(value) not in removed]
        if not kept:
            return None, removed
        return {**query, "values": kept}, removed
    if kind in ("and", "or") and set(query) == {"type", "filters"}:
        children = query["filters"]
        if not isinstance(children, list) or not children:
            return query, set()
        revised = [prune_query(child, removable) for child in children]
        removed = set().union(*(ips for _, ips in revised))
        if not removed:
            return query, set()
        if kind == "and" and any(child is None for child, _ in revised):
            return None, removed
        kept = [child for child, _ in revised if child is not None]
        if not kept:
            return None, removed
        return (kept[0] if len(kept) == 1 else {**query, "filters": kept}), removed
    return query, set()


def mentioned_ips(query: Any, ips: set[str]) -> set[str]:
    """Also identify broad/unsupported references for review, without editing."""
    if isinstance(query, list):
        return set().union(*(mentioned_ips(item, ips) for item in query))
    if not isinstance(query, dict):
        return set()
    found = set()
    if query.get("field") == "ip":
        values = query.get("values", [query.get("value")])
        if isinstance(values, list):
            for value in values:
                try:
                    network = ipaddress.ip_network(value, strict=False)
                except (ValueError, TypeError):
                    continue
                found.update(ip for ip in ips if ipaddress.ip_address(ip) in network)
    for value in query.values():
        if isinstance(value, (list, dict)):
            found |= mentioned_ips(value, ips)
    return found


def filter_body(record: dict) -> dict:
    if "short_query" not in record or not isinstance(record["short_query"], dict):
        raise ValueError("Inventory filter has no explicit short_query")
    if not record.get("name") or not record.get("app_scope_id"):
        raise ValueError("Inventory filter has no name or ownership scope")
    return {
        **{key: record[key] for key in FILTER_FIELDS if key in record},
        "query": record["short_query"],
    }


def policy_body(record: dict) -> dict:
    if any(key not in record for key in POLICY_FIELDS):
        raise ValueError("Policy lacks fields required for safe backup/recreation")
    if record["rank"] not in ("DEFAULT", "ABSOLUTE"):
        raise ValueError("Catch-all/unknown policy ranks are not pruned")
    if not isinstance(record["l4_params"], list):
        raise ValueError("Invalid policy service port list")
    for service in record["l4_params"]:
        if (
            not isinstance(service, dict)
            or any(key not in service for key in ("proto", "port"))
            or not isinstance(service["port"], list)
            or len(service["port"]) != 2
        ):
            raise ValueError("Unsupported policy service port schema")
    return {key: copy.deepcopy(record[key]) for key in POLICY_FIELDS}


def operation(kind, record, action, agents, relation, after=None, **extra):
    key = record.get("uuid") if kind == "agent" else record.get("id")
    identifier(key)
    return {
        "key": f"{kind}:{key}",
        "kind": kind,
        "id": key,
        "name": record.get("name", record.get("host_name", key)),
        "action": action,
        "agents": sorted(set(agents)),
        "relation": relation,
        "before": copy.deepcopy(record),
        "after": after,
        "dependencies": [],
        "status": "planned",
        **extra,
    }


def plan(api: PruneApi, cutoff: float) -> dict:
    agents = api.agents()
    selected = {a["uuid"]: a for a in agents if stale(a, cutoff)}
    skipped = []
    for agent in agents:
        if last_checkin(agent) is None:
            skipped.append(
                f"Agent {agent['uuid']}: missing/invalid last_config_fetch_at"
            )
    if not selected:
        return {"agents": [], "operations": [], "skipped": skipped, "cutoff": cutoff}
    inventory = api.inventory()
    observed = {ip for row in inventory if (ip := host_ip(row.get("ip")))}
    eligible = safe_ips(agents, cutoff, observed)
    all_ips = set().union(*(agent_ips(a) for a in selected.values()))
    for ip in sorted(all_ips - eligible.keys()):
        skipped.append(f"IP {ip}: currently observed, shared, or identity ambiguous")
    for ip in sorted(all_ips & observed):
        skipped.append(
            f"Inventory entry {ip}: related by agent interface; review only. "
            "No documented standalone inventory-record deletion API is used."
        )
    edits = {}
    filters = api.listing("/filters/inventories")
    for record in filters:
        identifier(record.get("id"))
        query = record.get("short_query")
        after, removed = prune_query(query, set(eligible))
        mentioned = mentioned_ips(query, all_ips)
        if not removed:
            if mentioned:
                skipped.append(
                    f"Filter {record['id']}: references "
                    f"{', '.join(sorted(mentioned))}; "
                    "protected IP or unsupported/negated/subnet expression"
                )
            continue
        try:
            body = filter_body(record)
        except ValueError as exc:
            skipped.append(f"Filter {record['id']}: {exc}")
            continue
        if record.get("primary") or record.get("public"):
            skipped.append(
                f"Filter {record['id']}: primary/public filter requires review"
            )
            continue
        owners = [uuid for ip in removed for uuid in eligible[ip]]
        edits[record["id"]] = operation(
            "filter",
            record,
            "delete" if after is None else "update",
            owners,
            f"explicit interface IP reference(s): {', '.join(sorted(removed))}",
            None if after is None else {**body, "query": after},
            ips=sorted(removed),
        )
    policies = api.policies()
    policy_ops = []
    for record in policies:
        related = [
            (side, edits[record[f"{side}_filter_id"]])
            for side in ("consumer", "provider")
            if record.get(f"{side}_filter_id") in edits
        ]
        if not related:
            continue
        owners = set().union(*(set(edit["agents"]) for _, edit in related))
        relation = "; ".join(
            f"{side} filter {edit['id']} -> {edit['relation']}"
            for side, edit in related
        )
        deleting = any(edit["action"] == "delete" for _, edit in related)
        try:
            policy_body(record)
        except ValueError as exc:
            skipped.append(f"Policy {record['id']}: {exc}")
            for _, edit in related:
                edit["blocked"] = f"policy {record['id']} cannot be safely restored"
            continue
        op = operation(
            "policy",
            record,
            "delete" if deleting else "acknowledge",
            owners,
            relation
            + (
                "; entire endpoint is stale"
                if deleting
                else "; shared policy preserved, targets narrowed via filter"
            ),
            ips=sorted(set().union(*(set(edit["ips"]) for _, edit in related))),
        )
        policy_ops.append(op)
        for _, edit in related:
            edit["dependencies"].append(op["key"])
    ops = policy_ops + list(edits.values())
    for op in policy_ops:
        blocked = [
            edit
            for edit in edits.values()
            if op["key"] in edit["dependencies"] and edit.get("blocked")
        ]
        if blocked:
            op["blocked"] = "a related inventory filter requires manual review"
    for ip, owners in eligible.items():
        # Search preserves exact record keys and never deletes a containing subnet.
        for record in api.api.search_static_labels(ip):
            if host_ip(record.get("key")) != ip:
                continue
            if not isinstance(record.get("value"), dict):
                raise ValueError(f"Malformed static inventory label record for {ip}")
            op = operation(
                "label",
                {**record, "id": record["key"]},
                "delete",
                owners,
                f"static inventory labels for exclusive agent interface {ip}",
                ips=[ip],
            )
            ops.append(op)
    for uuid, agent in selected.items():
        op = operation(
            "agent",
            agent,
            "delete",
            [uuid],
            f"last_config_fetch_at={agent['last_config_fetch_at']} <= cutoff {cutoff}",
            ips=[],
        )
        op["dependencies"] = [item["key"] for item in ops if uuid in item["agents"]]
        ops.append(op)
    keys = [op["key"] for op in ops]
    if len(keys) != len(set(keys)):
        raise ValueError("Duplicate objects in prune plan; no changes made")
    return {
        "agents": list(selected.values()),
        "operations": ops,
        "skipped": skipped,
        "cutoff": cutoff,
    }


def path_for(op: dict) -> str:
    prefix = {
        "agent": "/sensors/",
        "filter": "/filters/inventories/",
        "policy": "/policies/",
    }[op["kind"]]
    return prefix + identifier(op["id"])


def ensure_unchanged(api: PruneApi, op: dict) -> None:
    if op["kind"] == "label":
        matches = [
            r
            for r in api.api.search_static_labels(op["id"])
            if r.get("key") == op["id"]
        ]
        if len(matches) != 1 or matches[0].get("value") != op["before"]["value"]:
            raise ValueError("Static labels changed since preview; rerun dry-run")
        return
    current = api.get(path_for(op))
    if op["kind"] == "filter":
        equal = filter_body(current) == filter_body(op["before"])
    elif op["kind"] == "policy":
        equal = policy_body(current) == policy_body(op["before"])
    else:
        equal = current.get("uuid") == op["id"] and agent_ips(current) == agent_ips(
            op["before"]
        )
    if not equal:
        raise ValueError("Object changed since preview; rerun dry-run")
