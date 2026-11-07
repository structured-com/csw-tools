"""CLI presentation for manual-label Collection Rules compliance."""

import click
from rich.table import Table
from rich.text import Text

from csw_tools.commands.common import api_for
from csw_tools.commands.sync_collection_rules.api import CollectionRulesApi
from csw_tools.commands.sync_collection_rules.compliance import check_compliance
from csw_tools.context import AppContext, pass_app_context
from csw_tools.csw_api import CswApiError
from csw_tools.dashboard_context import dashboard_command
from csw_tools.interaction import interactive_command


def scope_or_vrf_name(
    _ctx: click.Context, parameter: click.Parameter, value: str | None
) -> str | None:
    if value is None:
        return None
    value = value.strip()
    if not value or value in (".", ".."):
        raise click.BadParameter(
            "must be a nonempty scope or VRF name other than '.' or '..'",
            param=parameter,
        )
    return value


@click.command("sync-collection-rules", context_settings={"max_content_width": 110})
@click.option(
    "--apply/--dry-run",
    default=False,
    show_default=True,
    help="Report compliance in dry-run mode. --apply is unavailable in phase 1.",
)
@click.option(
    "--root-app-scope-name",
    callback=scope_or_vrf_name,
    help="Root scope for manual-label download. Defaults to the dashboard name.",
)
@click.option(
    "--vrf-name",
    callback=scope_or_vrf_name,
    help="VRF for Collection Rules. Defaults to the dashboard name.",
)
@pass_app_context
@interactive_command
@dashboard_command
def command(
    app: AppContext,
    apply: bool,
    root_app_scope_name: str | None,
    vrf_name: str | None,
) -> None:
    """(DEV/TESTING) Check manual-label Collection Rules compliance.

    This module insures that any IP/subnets defined in manual user labels are
    populated correctly in Collection Rules.

    For flow learnt inventory items, if they are excluded by collection rules,
    inventory item will not exist. Therefore, inventory search and policies may not
    work correctly. This module will help ensure compliance of the Collection Rules.

    This is most important when non-RFC1918 IP's (public IP's, tunnel IP's) are
    needed for scopes and enforcement policy.

    Phase 1 checks complete IPv4/IPv6 coverage by the combined INCLUDE rules.
    EXCLUDE rules and rule precedence are not evaluated. Only entries needing
    action are shown. A completed report exits 0 even when coverage is missing;
    input and API failures exit nonzero. --apply is reserved for a later phase.

    Scope and VRF names default independently to the normalized SaaS tenant name,
    or the hostname/IP shown in the dashboard panel for other dashboards.
    Explicit names preserve case. CIDRs with host bits are normalized to networks.

    \b
    Examples:
      csw-tools -d my-company sync-collection-rules --dry-run
      csw-tools -d my-company sync-collection-rules \\
        --root-app-scope-name Production --vrf-name Default
    """

    if apply:
        raise click.UsageError(
            "--apply is not available in phase 1; use --dry-run for compliance."
        )
    assert app.dashboard is not None
    dashboard = app.dashboard
    default_name = dashboard.fqdn if dashboard.name == dashboard.url else dashboard.name
    root_app_scope_name = root_app_scope_name or default_name
    vrf_name = vrf_name or default_name

    app.console.print(Text(f"Root app scope: {root_app_scope_name}\nVRF: {vrf_name}"))
    app.console.print(
        "Dry-run compliance: combined INCLUDE coverage only. "
        "EXCLUDE rules and rule precedence are not evaluated."
    )
    try:
        api = CollectionRulesApi(api_for(app))
        entries = api.manual_labels(root_app_scope_name)
        includes = api.include_networks(vrf_name)
        findings = check_compliance(entries, includes)
    except (CswApiError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc

    if findings:
        table = Table(
            title="Collection Rules compliance: action needed", show_lines=True
        )
        table.add_column("IP/subnet", overflow="fold")
        table.add_column("CSV rows", overflow="fold")
        table.add_column("Issue", overflow="fold")
        for finding in findings:
            table.add_row(
                Text(finding.entry.display),
                Text(", ".join(str(row) for row in finding.entry.rows)),
                finding.issue,
            )
        app.console.print(table)
    elif entries:
        app.console.print(
            "All manual-label IPs/subnets have complete INCLUDE coverage."
        )
    else:
        app.console.print("No manual-label IPs found.")
    app.console.print(
        f"Summary: Checked {len(entries)} unique entries | "
        f"Covered {len(entries) - len(findings)} | Need action {len(findings)}"
    )
