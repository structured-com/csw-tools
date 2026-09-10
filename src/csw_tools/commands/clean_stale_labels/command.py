"""Safely clean static labels whose workloads are absent from inventory."""

from __future__ import annotations

import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path

import click
from rich.table import Table

from csw_tools.change_control import backup_path, load_backup, new_backup, write_backup
from csw_tools.commands.common import (
    DEFAULT_BACKUP_DIRECTORY,
    api_for,
    command_error,
    require_apply_for_rollback,
)
from csw_tools.context import AppContext, pass_app_context
from csw_tools.csw_api import CswApi
from csw_tools.dashboard_context import dashboard_command
from csw_tools.interaction import interactive_command

COMMAND_NAME = "clean-stale-labels"
DEFAULT_IP_RANGES = ("0.0.0.0/0", "::/0")


def parse_updated_at(value: object) -> datetime | None:
    if isinstance(value, (int, float)):
        timestamp = float(value)
        if timestamp > 10_000_000_000:
            timestamp /= 1000
        try:
            return datetime.fromtimestamp(timestamp, UTC)
        except (OSError, OverflowError, ValueError):
            return None
    if isinstance(value, str):
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
            return (
                parsed.astimezone(UTC) if parsed.tzinfo else parsed.replace(tzinfo=UTC)
            )
        except ValueError:
            return None
    return None


IPAddress = ipaddress.IPv4Address | ipaddress.IPv6Address


def is_observed(label_key: str, observed: set[IPAddress]) -> bool:
    try:
        network = ipaddress.ip_network(label_key, strict=False)
    except ValueError:
        return False
    return any(
        address in network for address in observed if address.version == network.version
    )


def plan_cleanup(
    api: CswApi,
    *,
    minimum_age: int,
    ip_ranges: tuple[str, ...],
    page_size: int,
    now: datetime | None = None,
) -> tuple[list[dict[str, object]], int]:
    observed: set[IPAddress] = set()
    for workload in api.inventory(dimensions=["ip"], page_size=page_size):
        ip = workload.get("ip")
        if isinstance(ip, str):
            try:
                observed.add(ipaddress.ip_address(ip))
            except ValueError:
                continue

    unique_records: dict[str, dict[str, object]] = {}
    for ip_range in ip_ranges:
        for record in api.search_static_labels(ip_range):
            key = record.get("key")
            if isinstance(key, str):
                unique_records[key] = record

    cutoff = (now or datetime.now(UTC)) - timedelta(days=minimum_age)
    operations: list[dict[str, object]] = []
    missing_timestamp = 0
    for key, record in unique_records.items():
        updated_at = parse_updated_at(record.get("updatedAt"))
        if updated_at is None:
            missing_timestamp += 1
            continue
        if updated_at > cutoff or is_observed(key, observed):
            continue
        attributes = record.get("value")
        if not isinstance(attributes, dict):
            continue
        operations.append(
            {
                "ip": key,
                "before": attributes,
                "updated_at": updated_at.isoformat(),
            }
        )
    return operations, missing_timestamp


def rollback_cleanup(api: CswApi, backup: dict[str, object]) -> int:
    operations = backup["operations"]
    assert isinstance(operations, list)
    restored = 0
    for operation in reversed(operations):
        if not isinstance(operation, dict) or not operation.get("attempted"):
            continue
        ip = operation.get("ip")
        before = operation.get("before")
        if isinstance(ip, str) and isinstance(before, dict):
            api.set_static_label(ip, before)
            restored += 1
    return restored


@click.command(COMMAND_NAME, context_settings={"max_content_width": 110})
@click.option(
    "--minimum-age",
    type=click.IntRange(min=1),
    default=30,
    show_default=True,
    metavar="DAYS",
    help=(
        "Minimum age of a static label record before it can be removed. A label "
        "is removed only when its workload is also absent from current inventory."
    ),
)
@click.option(
    "--ip-range",
    "ip_ranges",
    multiple=True,
    metavar="CIDR",
    help=(
        "Limit label discovery to this IPv4/IPv6 CIDR. Repeat as needed. "
        "Defaults to all IPv4 and IPv6 labels."
    ),
)
@click.option(
    "--page-size",
    type=click.IntRange(min=1),
    default=500,
    show_default=True,
    help="Current inventory records requested per API page.",
)
@click.option(
    "--backup-dir",
    type=click.Path(path_type=Path, file_okay=False),
    default=DEFAULT_BACKUP_DIRECTORY,
    show_default=True,
    help="Directory for the automatic JSON backup created before deletion.",
)
@click.option(
    "--apply/--dry-run",
    default=False,
    show_default=True,
    help="Delete eligible label records, or only display what would be deleted.",
)
@click.option(
    "--rollback",
    type=click.Path(path_type=Path, dir_okay=False, exists=True),
    help=(
        "Restore deleted labels from a backup created by this command; "
        "requires --apply."
    ),
)
@pass_app_context
@interactive_command
@dashboard_command
def command(
    app: AppContext,
    minimum_age: int,
    ip_ranges: tuple[str, ...],
    page_size: int,
    backup_dir: Path,
    apply: bool,
    rollback: Path | None,
) -> None:
    """(DEV/TESTING) Remove static labels for workloads absent from observation.

    This command compares scope-independent static workload label records with
    current CSW inventory. A record is eligible only when (1) no currently
    observed inventory IP belongs to that label's IP/subnet and (2) the label's
    updatedAt timestamp is at least --minimum-age days old (30 by default).
    Records without a usable updatedAt timestamp are never removed.

    Deletion removes the entire static label record for the IP/subnet. The default
    is --dry-run. --apply writes all original attributes to a timestamped JSON
    backup before the first deletion and updates the backup after every success.
    Use --apply --rollback BACKUP to recreate all records deleted by that run.

    \b
    Examples:
      csw-tools -d acme clean-stale-labels
      csw-tools -d acme clean-stale-labels --minimum-age 60 --apply
      csw-tools -d acme clean-stale-labels --ip-range 10.0.0.0/8 --dry-run
      csw-tools -d acme clean-stale-labels --apply --rollback BACKUP.json
    """

    try:
        require_apply_for_rollback(apply, rollback)
        api = api_for(app)
        assert app.dashboard is not None
        if rollback is not None:
            backup = load_backup(
                rollback, command=COMMAND_NAME, dashboard=app.dashboard
            )
            count = rollback_cleanup(api, backup)
            app.console.print(
                f"[green]Restored {count} static label record(s).[/green]"
            )
            return

        selected_ranges = ip_ranges or DEFAULT_IP_RANGES
        for ip_range in selected_ranges:
            try:
                ipaddress.ip_network(ip_range, strict=False)
            except ValueError as exc:
                raise click.BadParameter(
                    f"invalid CIDR: {ip_range}", param_hint="--ip-range"
                ) from exc
        operations, missing_timestamp = plan_cleanup(
            api,
            minimum_age=minimum_age,
            ip_ranges=selected_ranges,
            page_size=page_size,
        )
        table = Table(
            "Workload/IP range",
            "Last label update",
            "Labels",
            title="Stale label cleanup",
        )
        for operation in operations:
            table.add_row(
                str(operation["ip"]),
                str(operation["updated_at"]),
                str(operation["before"]),
            )
        app.console.print(table)
        if missing_timestamp:
            app.error_console.print(
                f"[yellow]Skipped {missing_timestamp} label record(s) without "
                "a usable updatedAt timestamp.[/yellow]"
            )
        if not operations:
            app.console.print("No stale label records are eligible for removal.")
            return
        if not apply:
            app.console.print(
                "[yellow]Dry run: no label records were deleted.[/yellow]"
            )
            return

        backup = new_backup(
            command=COMMAND_NAME, dashboard=app.dashboard, operations=operations
        )
        path = backup_path(backup_dir, COMMAND_NAME)
        write_backup(path, backup)
        for operation in operations:
            operation["attempted"] = True
            write_backup(path, backup)
            api.delete_static_label(str(operation["ip"]))
            operation["applied"] = True
            write_backup(path, backup)
        app.console.print(
            f"[green]Deleted {len(operations)} stale label record(s).[/green]"
        )
        app.console.print(f"Backup: {path}")
    except click.ClickException:
        raise
    except Exception as exc:
        raise command_error(exc) from exc
