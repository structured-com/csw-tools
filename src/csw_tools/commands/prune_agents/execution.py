"""Journaled application and limited recovery of prune operations."""

from __future__ import annotations

import copy
from pathlib import Path

from csw_tools.change_control import write_backup
from csw_tools.commands.prune_agents.api import PruneApi, identifier
from csw_tools.commands.prune_agents.planner import (
    ensure_unchanged,
    filter_body,
    host_ip,
    path_for,
    policy_body,
    safe_ips,
    stale,
)


def describe(op: dict) -> str:
    return (
        f"{op['action'].upper()} {op['kind']} {op['name']} ({op['id']})\n"
        f"Related stale agent(s): {', '.join(op['agents'])}\n"
        f"Relation: {op['relation']}"
    )


def apply_plan(api: PruneApi, backup: dict, path: Path, confirm, report) -> dict:
    operations = backup["operations"]
    cutoff = backup["cutoff"]
    counts = {"deleted": 0, "updated": 0, "failed": 0, "skipped": 0}
    approved = set()
    # Obtain every decision before any writes: a declined shared-policy impact
    # must never be discovered after another endpoint has already been deleted.
    for op in operations:
        if op.get("blocked"):
            op.update(status="skipped", reason=op["blocked"])
        elif confirm(op):
            approved.add(op["key"])
        else:
            op.update(status="skipped", reason="declined")
    # Declining an agent also protects every related operation.
    declined_agents = {
        op["id"]
        for op in operations
        if op["kind"] == "agent" and op["key"] not in approved
    }
    for op in operations:
        if op["kind"] == "policy" and any(
            op["key"] in other["dependencies"] and other["key"] not in approved
            for other in operations
            if other["kind"] == "filter"
        ):
            op.update(status="skipped", reason="related filter was declined/blocked")
    write_backup(path, backup)
    completed = set()
    for op in operations:
        if op["status"] != "skipped":
            if set(op["agents"]) & declined_agents:
                op.update(status="skipped", reason="related agent was declined")
            elif not set(op["dependencies"]) <= completed:
                op.update(status="skipped", reason="related operation not completed")
        if op["status"] == "skipped":
            counts["skipped"] += 1
            report(f"SKIPPED {op['key']}: {op['reason']}")
            write_backup(path, backup)
            continue
        try:
            for uuid in op["agents"]:
                current = api.get("/sensors/" + identifier(uuid))
                if current.get("uuid") != uuid or not stale(current, cutoff):
                    raise ValueError(f"Agent {uuid} is no longer confirmed stale")
            if op["ips"]:
                # Recheck all owners and current observation after confirmation.
                agents = api.agents()
                observed = {
                    ip for row in api.inventory() if (ip := host_ip(row.get("ip")))
                }
                eligible = safe_ips(agents, cutoff, observed)
                if any(
                    ip not in eligible or not set(eligible[ip]) <= set(op["agents"])
                    for ip in op["ips"]
                ):
                    raise ValueError("IP ownership/observation changed since preview")
            ensure_unchanged(api, op)
            if op["kind"] == "policy":
                for endpoint in operations:
                    if (
                        endpoint["kind"] == "filter"
                        and op["key"] in endpoint["dependencies"]
                    ):
                        ensure_unchanged(api, endpoint)
                workspace = api.get(
                    "/applications/" + identifier(op["before"]["application_id"])
                )
                if f"v{workspace.get('latest_adm_version')}" != op["before"]["version"]:
                    raise ValueError("Workspace version changed since preview")
            if op["kind"] == "filter":
                expected = {
                    item["id"]
                    for item in operations
                    if item["key"] in op["dependencies"]
                    and item["status"] == "acknowledged"
                }
                current_policies = [
                    policy
                    for policy in api.policies()
                    if op["id"]
                    in (
                        policy.get("consumer_filter_id"),
                        policy.get("provider_filter_id"),
                    )
                ]
                if {policy["id"] for policy in current_policies} != expected:
                    raise ValueError("Filter policy dependencies changed since preview")
                for policy in current_policies:
                    previous = next(
                        item
                        for item in operations
                        if item["key"] == "policy:" + policy["id"]
                    )
                    if policy_body(policy) != policy_body(previous["before"]):
                        raise ValueError("Shared policy changed since preview")
        except Exception as exc:
            op.update(status="failed", error=str(exc))
            counts["failed"] += 1
            report(f"FAILED {op['key']}: {exc}")
            write_backup(path, backup)
            continue
        if op["action"] == "acknowledge":
            op["status"] = "acknowledged"
            completed.add(op["key"])
            write_backup(path, backup)
            continue
        op["status"] = "attempting"
        write_backup(path, backup)  # Failure here must prevent this API mutation.
        try:
            if op["kind"] == "label":
                api.mutate("delete", "/si_inventory/tags", {"ip": op["id"]})
            else:
                api.mutate(
                    "put" if op["action"] == "update" else "delete",
                    path_for(op),
                    op["after"],
                )
            verify_applied(api, op)
            op["status"] = "applied"
            counts["updated" if op["action"] == "update" else "deleted"] += 1
            completed.add(op["key"])
        except Exception as exc:
            # A timeout can mean the server applied the request. Never retry
            # automatically or claim it is safe to recreate this object.
            op.update(status="uncertain", error=str(exc))
            counts["failed"] += 1
            report(
                f"FAILED/UNCERTAIN {op['key']}: {exc}; inspect dashboard before retry"
            )
        write_backup(path, backup)
    backup["summary"] = counts
    write_backup(path, backup)
    return counts


def verify_applied(api: PruneApi, op: dict) -> None:
    """Do not interpret a 2xx dependency response as successful deletion."""
    if op["action"] == "update":
        if filter_body(api.get(path_for(op))) != op["after"]:
            raise ValueError("Filter update could not be verified")
        return
    if op["kind"] == "agent":
        present = any(
            a["uuid"] == op["id"] and not a.get("deleted_at") for a in api.agents()
        )
    elif op["kind"] == "filter":
        present = any(
            r.get("id") == op["id"] for r in api.listing("/filters/inventories")
        )
    elif op["kind"] == "policy":
        present = any(r.get("id") == op["id"] for r in api.policies())
    else:
        present = any(
            r.get("key") == op["id"] for r in api.api.search_static_labels(op["id"])
        )
    if present:
        raise ValueError(
            "Deletion not verified; object still present (possible dependents)"
        )


def validate_recovery(backup: dict) -> None:
    """Reject edited/foreign operation routes before any recovery requests."""
    if backup.get("prune_schema") != 1:
        raise ValueError("Unsupported prune-agents backup schema")
    if not isinstance(backup.get("operations"), list):
        raise ValueError("Invalid operations in backup")
    keys = set()
    for op in backup["operations"]:
        if not isinstance(op, dict) or op.get("kind") not in (
            "agent",
            "filter",
            "policy",
            "label",
        ):
            raise ValueError("Unsupported backup operation")
        identifier(op.get("id"))
        if op.get("key") != f"{op['kind']}:{op['id']}" or op["key"] in keys:
            raise ValueError("Invalid/duplicate backup operation key")
        keys.add(op["key"])
        if op.get("action") not in ("delete", "update", "acknowledge"):
            raise ValueError("Invalid backup action")
        if not isinstance(op.get("before"), dict):
            raise ValueError("Missing original object in backup")
        original_id = (
            op["before"].get("uuid")
            if op["kind"] == "agent"
            else op["before"].get("id")
        )
        if original_id != op["id"]:
            raise ValueError("Backup object identity mismatch")
        allowed = {
            "agent": {"delete"},
            "label": {"delete"},
            "filter": {"delete", "update"},
            "policy": {"delete", "acknowledge"},
        }
        if op["action"] not in allowed[op["kind"]]:
            raise ValueError("Invalid action for object kind")
        if not isinstance(op.get("agents"), list) or any(
            not isinstance(uuid, str) for uuid in op["agents"]
        ):
            raise ValueError("Invalid agent relationships in backup")
        if op["kind"] == "filter":
            filter_body(op["before"])
            if op["action"] == "update" and not isinstance(op.get("after"), dict):
                raise ValueError("Missing expected filter state")
        if op["kind"] == "policy":
            policy_body(op["before"])
        if op["kind"] == "label" and (
            host_ip(op["id"]) is None or not isinstance(op["before"].get("value"), dict)
        ):
            raise ValueError("Invalid static label backup")


def recover(
    api: PruneApi,
    source: dict,
    source_path: Path,
    journal: dict,
    journal_path: Path,
    confirm,
    report,
) -> dict:
    """Best-effort recovery, keeping recreated IDs and steps durable.

    The original backup tracks completion to prevent duplicate creates on a
    subsequent rollback. A separate journal contains pre-recovery snapshots.
    Uncertain mutations are deliberately manual-recovery-only.
    """
    validate_recovery(source)
    counts = {"restored": 0, "failed": 0, "skipped": 0}
    operations = source["operations"]
    mapping = {
        op["id"]: op["restored_id"] for op in operations if op.get("restored_id")
    }
    # Restore endpoint filters before policies which refer to their new IDs.
    ordered = sorted(
        operations,
        key=lambda op: {"filter": 0, "label": 1, "policy": 2, "agent": 3}[op["kind"]],
    )
    for op in ordered:
        if op.get("status") in ("attempting", "uncertain") or op.get("recovering"):
            report(f"MANUAL RECOVERY {op['key']}: prior request outcome is uncertain")
            counts["failed"] += 1
            continue
        if op.get("status") != "applied" or op.get("recovered"):
            continue
        if op["kind"] == "agent":
            report(f"NOT RESTORABLE {op['key']}: agent decommissioning is destructive")
            counts["skipped"] += 1
            continue
        if not confirm({**op, "action": "restore"}):
            counts["skipped"] += 1
            continue
        entry = {"key": op["key"], "original": copy.deepcopy(op), "status": "planned"}
        journal["operations"].append(entry)
        try:
            if op["kind"] == "filter" and op["action"] == "update":
                current = api.get(path_for(op))
                entry["before"] = current
                if filter_body(current) != op["after"]:
                    raise ValueError(
                        "Filter changed since pruning; manual merge required"
                    )
            elif op["kind"] == "label":
                matches = api.api.search_static_labels(op["id"])
                entry["before"] = matches
                if any(r.get("key") == op["id"] for r in matches):
                    raise ValueError("Label record now exists; refusing to overwrite")
            else:
                # Listing avoids interpreting every 404/auth failure as absence.
                records = (
                    api.listing("/filters/inventories")
                    if op["kind"] == "filter"
                    else api.policies()
                )
                if any(
                    r.get("id") in (op["id"], op.get("restored_id")) for r in records
                ):
                    raise ValueError(
                        "Object already exists; refusing duplicate recreation"
                    )
                entry["before"] = None
                if op["kind"] == "policy":
                    for side in ("consumer", "provider"):
                        old = op["before"][f"{side}_filter_id"]
                        for endpoint in operations:
                            if (
                                endpoint["kind"] == "filter"
                                and endpoint["id"] == old
                                and endpoint.get("status") == "applied"
                                and not endpoint.get("recovered")
                            ):
                                raise ValueError(
                                    "Related filter must be restored first"
                                )
                    workspace = api.get(
                        "/applications/" + identifier(op["before"]["application_id"])
                    )
                    if (
                        f"v{workspace.get('latest_adm_version')}"
                        != op["before"]["version"]
                    ):
                        raise ValueError(
                            "Workspace version changed; manual recovery required"
                        )
        except Exception as exc:
            entry.update(status="failed", error=str(exc))
            counts["failed"] += 1
            report(f"RECOVERY FAILED {op['key']}: {exc}")
            write_backup(journal_path, journal)
            continue
        entry["status"] = "attempting"
        write_backup(journal_path, journal)
        op["recovering"] = True
        write_backup(source_path, source)
        try:
            if op["kind"] == "label":
                api.mutate(
                    "post",
                    "/si_inventory/tags",
                    {"ip": op["id"], "attributes": op["before"]["value"]},
                )
            elif op["kind"] == "filter":
                if op["action"] == "update":
                    api.mutate("put", path_for(op), filter_body(op["before"]))
                else:
                    result = api.mutate(
                        "post", "/filters/inventories", filter_body(op["before"])
                    )
                    new_id = result.get("id") if isinstance(result, dict) else None
                    identifier(new_id)
                    op["restored_id"] = new_id
                    mapping[op["id"]] = new_id
            else:
                body = policy_body(op["before"])
                services = body.pop("l4_params")
                app_id = body.pop("application_id")
                for side in ("consumer", "provider"):
                    key = f"{side}_filter_id"
                    body[key] = mapping.get(body[key], body[key])
                result = api.mutate(
                    "post", f"/applications/{identifier(app_id)}/policies", body
                )
                new_id = result.get("id") if isinstance(result, dict) else None
                identifier(new_id)
                op["restored_id"] = new_id
                write_backup(source_path, source)
                for service in services:
                    payload = {
                        "version": body["version"],
                        "proto": service["proto"],
                        "start_port": service["port"][0],
                        "end_port": service["port"][1],
                    }
                    if "description" in service:
                        payload["description"] = service["description"]
                    port = api.mutate(
                        "post", f"/policies/{identifier(new_id)}/l4_params", payload
                    )
                    if service.get("approved"):
                        port_id = port.get("id") if isinstance(port, dict) else None
                        api.mutate(
                            "put",
                            f"/policies/{identifier(new_id)}/l4_params/"
                            + identifier(port_id),
                            {"approved": True},
                        )
            op.update(recovering=False, recovered=True)
            entry["status"] = "restored"
            counts["restored"] += 1
        except Exception as exc:
            entry.update(status="uncertain", error=str(exc))
            counts["failed"] += 1
            report(f"RECOVERY UNCERTAIN {op['key']}: {exc}; inspect dashboard")
        write_backup(source_path, source)
        write_backup(journal_path, journal)
    journal["summary"] = counts
    write_backup(journal_path, journal)
    return counts
