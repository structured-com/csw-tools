"""Review and prune stale agents with journaled, explicit destructive actions."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from functools import wraps
from pathlib import Path

import click

from csw_tools import interaction
from csw_tools.change_control import backup_path, load_backup, new_backup, write_backup
from csw_tools.commands.common import (
    DEFAULT_BACKUP_DIRECTORY,
    api_for,
    command_error,
    require_apply_for_rollback,
)
from csw_tools.commands.prune_agents.api import PruneApi
from csw_tools.commands.prune_agents.execution import (
    apply_plan,
    describe,
    recover,
    validate_recovery,
)
from csw_tools.commands.prune_agents.planner import plan
from csw_tools.context import AppContext, pass_app_context
from csw_tools.dashboard_context import dashboard_command

COMMAND_NAME = "prune-agents"
WARNING = (
    "DESTRUCTIVE: deleting an agent decommissions it. There is no true rollback "
    "for agents or historical telemetry. Backups permit only limited recovery of "
    "supported related objects. No policy publish/enforce action is performed; "
    "shared inventory-filter changes can still affect their existing consumers."
)


def prune_interactive(function):
    @wraps(function)
    def guarded(app, *args, **kwargs):
        if not interaction.is_interactive_terminal():
            if not kwargs.get("noconfirm"):
                raise click.ClickException(
                    "This command requires an interactive terminal unless --noconfirm "
                    "is supplied. --noconfirm does not imply --apply."
                )
            if app.dashboard is None:
                raise click.UsageError(
                    "Non-interactive pruning requires --dashboard or config"
                )
        return function(app, *args, **kwargs)

    return guarded


@click.command(COMMAND_NAME, context_settings={"max_content_width": 110})
@click.option(
    "--lastdate",
    type=click.IntRange(min=1, max=9_999_999_999),
    metavar="EPOCH",
    help="Inclusive stale cutoff in Unix epoch seconds (UTC), not milliseconds. "
    "Default: run time minus 30 days. Future dates are rejected.",
)
@click.option(
    "--noconfirm",
    is_flag=True,
    help="Skip individual confirmations, but still display all changes. "
    "Does not enable writes without --apply.",
)
@click.option(
    "--apply/--dry-run",
    default=False,
    show_default=True,
    help="Apply destructive changes, or preview only (default).",
)
@click.option(
    "--page-size",
    type=click.IntRange(min=1),
    default=500,
    show_default=True,
    help="Number of agents/inventory/policies requested per API page.",
)
@click.option(
    "--backup-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=DEFAULT_BACKUP_DIRECTORY,
    show_default=True,
    help="Directory for pre-change backups and per-operation result journals.",
)
@click.option(
    "--rollback",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help="Limited recovery of related objects from BACKUP; requires --apply. "
    "Cannot restore decommissioned agents. Never retries uncertain writes.",
)
@pass_app_context
@prune_interactive
@dashboard_command
def command(
    app: AppContext,
    lastdate: int | None,
    noconfirm: bool,
    apply: bool,
    page_size: int,
    backup_dir: Path,
    rollback: Path | None,
) -> None:
    """Review and destructively prune stale agents and explicit related objects.

    DESTRUCTIVE: agent decommissioning has no true rollback. Saving agent details
    does NOT allow re-registering the agent or restoring historical telemetry.
    Start with --dry-run (the default). --apply shows every proposed action,
    then asks about each element; answers default to No. --noconfirm suppresses
    these prompts but still prints the complete plan and destructive warning.

    Staleness uses the agent API's last_config_fetch_at (last configuration
    contact), NOT label age or software installation time. The cutoff is run
    time minus 30 days unless --lastdate EPOCH is supplied. Comparison is <=,
    in UTC epoch seconds. Missing, zero, invalid or future contact times are not
    treated as stale. Already decommissioned agents are skipped. Contact and
    ownership are checked again after confirmation and before changes.

    Relationships are shown by agent UUID, interface IP, filter ID and policy
    consumer/provider role. Exact positive IP equality/membership and AND/OR
    filters are narrowed without broadening their logic. Shared policies are
    preserved through their narrowed filters; policies whose entire endpoint
    is removed are deleted first. Declined/failed prerequisites protect dependent
    objects. IPs shared by agents or present in current inventory are protected.
    Primary/public filters, subnet/negated references and ambiguous schemas are
    left for manual review. Scopes, ADM clusters, historical/enforced policy
    versions, and label-based membership are not automatically rewritten.

    Inventory cleanup removes exact per-IP scope-independent static label
    records. Observed inventory entries are reported for review, not deleted:
    no undocumented standalone inventory-record deletion endpoint is used.
    Policies are processed only in each visible workspace's latest v* version.
    The command never publishes or enforces policies. Shared filter edits may
    nevertheless affect existing consumers. Run with complete visibility of
    agents, inventory, filters and workspaces in the intended administrative scope.

    --apply saves a timestamped JSON backup BEFORE any mutation and journals each
    result. API failures are reported and independent actions continue. A backup
    write failure stops further mutations. Protect backups: they contain workload
    and policy data. --apply --rollback BACKUP restores supported filters, labels
    and policy definitions where possible; recreated objects receive new IDs.
    Changed objects and uncertain requests require manual recovery. Agents cannot
    be restored. Recovery creates its own pre-change journal and does not enforce.

    \b
    Examples:
      csw-tools -d acme prune-agents --dry-run
      csw-tools -d acme prune-agents --lastdate 1788220800 --apply
      csw-tools -d acme prune-agents --apply --noconfirm
      csw-tools -d acme prune-agents --apply --rollback BACKUP.json
      csw-tools -d https://csw.example.org prune-agents --dry-run
    """
    try:
        require_apply_for_rollback(apply, rollback)
        now = datetime.now(UTC)
        cutoff = (
            float(lastdate)
            if lastdate is not None
            else (now - timedelta(days=30)).timestamp()
        )
        if cutoff > now.timestamp():
            raise click.BadParameter(
                "cutoff cannot be in the future", param_hint="--lastdate"
            )
        if rollback is not None and lastdate is not None:
            raise click.UsageError("--lastdate cannot be combined with --rollback")
        app.console.print(WARNING, style="bold yellow", markup=False)
        api = PruneApi(api_for(app), page_size)
        assert app.dashboard is not None

        def report(message):
            app.error_console.print(message, markup=False, highlight=False)

        def confirm(op):
            if noconfirm:
                return True
            return click.confirm(
                describe(op) + "\nProceed with this element?", default=False
            )

        if rollback is not None:
            source = load_backup(
                rollback, command=COMMAND_NAME, dashboard=app.dashboard
            )
            validate_recovery(source)
            if source.get("dashboard_url") != app.dashboard.url:
                raise ValueError("Backup dashboard URL does not match active dashboard")
            for op in source["operations"]:
                if op.get("status") in ("applied", "attempting", "uncertain"):
                    app.console.print(
                        describe({**op, "action": "review recovery"}), markup=False
                    )
            journal = new_backup(
                command=COMMAND_NAME + "-recovery",
                dashboard=app.dashboard,
                operations=[],
            )
            path = backup_path(backup_dir, COMMAND_NAME + "-recovery")
            write_backup(path, journal)
            app.console.print(f"Recovery journal: {path}", markup=False)
            counts = recover(api, source, rollback, journal, path, confirm, report)
            app.console.print(
                "Recovery summary: "
                + ", ".join(f"{key}={value}" for key, value in counts.items())
            )
            if counts["failed"]:
                raise click.ClickException(
                    "Some recovery actions failed; inspect the journal"
                )
            return

        planned = plan(api, cutoff)
        app.console.print(
            f"Stale cutoff: {datetime.fromtimestamp(cutoff, UTC).isoformat()} "
            f"({cutoff:g} epoch seconds)"
        )
        for agent in planned["agents"]:
            app.console.print(
                f"STALE AGENT {agent.get('host_name', '')} ({agent['uuid']}) "
                f"last contact={agent['last_config_fetch_at']}",
                markup=False,
            )
        for op in planned["operations"]:
            app.console.print(describe(op), markup=False, highlight=False)
            if op["after"] is not None:
                app.console.print(
                    "Proposed value: " + json.dumps(op["after"], sort_keys=True),
                    markup=False,
                    highlight=False,
                )
            if op.get("blocked"):
                report("BLOCKED: " + op["blocked"])
        for reason in planned["skipped"]:
            report("REVIEW/SKIP: " + reason)
        app.console.print(
            f"Plan: {len(planned['agents'])} stale agents; "
            f"{len(planned['operations'])} actions/impact confirmations; "
            f"{len(planned['skipped'])} review notices."
        )
        if not apply:
            app.console.print("Dry run: no objects changed.")
            return
        if not planned["operations"]:
            app.console.print("No stale agents eligible for pruning.")
            return
        backup = new_backup(
            command=COMMAND_NAME,
            dashboard=app.dashboard,
            operations=planned["operations"],
        )
        backup.update(
            prune_schema=1,
            dashboard_url=app.dashboard.url,
            cutoff=cutoff,
            review_notices=planned["skipped"],
        )
        path = backup_path(backup_dir, COMMAND_NAME)
        write_backup(path, backup)
        app.console.print(f"Backup and result/error journal: {path}", markup=False)
        counts = apply_plan(api, backup, path, confirm, report)
        app.console.print(
            "Summary: " + ", ".join(f"{key}={value}" for key, value in counts.items())
        )
        if counts["failed"]:
            raise click.ClickException(
                "Some prune actions failed; inspect the result/error journal"
            )
    except (click.ClickException, click.Abort):
        raise
    except Exception as exc:
        raise command_error(exc) from exc
