"""CLI presentation for manual-label Collection Rules compliance."""

import click
from rich.table import Table
from rich.text import Text

from csw_tools.commands.common import api_for
from csw_tools.commands.sync_collection_rules.api import CollectionRulesApi
from csw_tools.commands.sync_collection_rules.apply import apply_collection_rules
from csw_tools.commands.sync_collection_rules.compliance import (
    Finding,
    LabelEntry,
    check_compliance,
    parse_collection_rules,
    plan_additions,
    require_final_catch_alls,
)
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


def display_plan(
    app: AppContext,
    entries: list[LabelEntry],
    findings: list[Finding],
    additions: list[dict[str, object]],
) -> None:
    if findings:
        table = Table(
            title="Collection Rules compliance: action needed", show_lines=True
        )
        table.add_column("IP/subnet", overflow="fold")
        table.add_column("CSV rows", overflow="fold")
        table.add_column("Issue", overflow="fold")
        table.add_column("Prepend INCLUDE", overflow="fold")
        for finding, addition in zip(findings, additions, strict=True):
            table.add_row(
                Text(finding.entry.display),
                Text(", ".join(str(row) for row in finding.entry.rows)),
                finding.issue,
                Text(str(addition["subnet"])),
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


@click.command("sync-collection-rules", context_settings={"max_content_width": 110})
@click.option(
    "--apply/--dry-run",
    default=False,
    show_default=True,
    help="Preview additions or prepend INCLUDE rules with --apply.",
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
    """Check (and fix) Collection Rules against defined IP sources, such as user labels.

    This module ensures that any IPs/subnets defined in manual user labels are
    populated correctly in Collection Rules.

    Flow-learnt inventory items excluded by Collection Rules will not exist in
    inventory. Therefore, inventory search and policies may not work correctly.
    This module helps ensure Collection Rules compliance.

    This is most important when non-RFC1918 IPs (public IPs, tunnel IPs) are
    needed for scopes and enforcement policy.

    When applying changes, INCLUDE statements will be added at the beginning of
    the Collection Rules list (no other changes to rules will be made).

    Before applying, the original last two rules must be EXCLUDE 0.0.0.0/0 and
    EXCLUDE ::/0, in either order. This is best practice, so any incorrect rules
    must be manually corrected first.

    The backend API requires both a VRF name (for Collection Rules) and a root
    app scope name (for user labels). These are likely the same as your dashboard
    name, so that will be attempted by default.
    """

    assert app.dashboard is not None
    dashboard = app.dashboard
    default_name = dashboard.fqdn if dashboard.name == dashboard.url else dashboard.name
    root_app_scope_name = root_app_scope_name or default_name
    vrf_name = vrf_name or default_name

    app.console.print(Text(f"Root app scope: {root_app_scope_name}\nVRF: {vrf_name}"))
    app.console.print(
        "Effective INCLUDE coverage: first matching rule wins, including EXCLUDE."
    )
    try:
        api = CollectionRulesApi(api_for(app))
        entries = api.manual_labels(root_app_scope_name)
        current = api.rules(vrf_name)
        rules = parse_collection_rules(current)
        findings = check_compliance(entries, rules)
        additions = plan_additions(findings)
        try:
            require_final_catch_alls(rules)
        except ValueError as exc:
            if apply:
                raise
            app.error_console.print(Text(f"Apply blocked: {exc}"))

        display_plan(app, entries, findings, additions)
        if not apply:
            app.console.print(
                f"Dry run: would prepend {len(additions)} INCLUDE rule(s). "
                "No changes were submitted."
            )
            return
        if not additions:
            app.console.print("No additions needed; no POST or JSON history created.")
            return
        apply_collection_rules(
            app,
            api,
            root_app_scope_name=root_app_scope_name,
            vrf_name=vrf_name,
            current=current,
            additions=additions,
        )
        app.console.print(
            f"Applied and verified {len(additions)} INCLUDE addition(s); "
            "all manual-label IPs/subnets now have complete INCLUDE coverage."
        )
    except (CswApiError, ValueError) as exc:
        raise click.ClickException(str(exc)) from exc
