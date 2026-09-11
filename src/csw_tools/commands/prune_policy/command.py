"""Placeholder for the prune-policy utility."""

import click

from csw_tools.context import AppContext, pass_app_context
from csw_tools.dashboard_context import dashboard_command
from csw_tools.interaction import interactive_command


@click.command("prune-policy")
@pass_app_context
@interactive_command
@dashboard_command
def command(app: AppContext) -> None:
    """(DEV/TESTING) Run the prune-policy utility (not implemented yet)."""

    app.error_console.print(
        "[yellow]The 'prune-policy' command is not implemented yet.[/yellow]"
    )
    raise click.exceptions.Exit(1)
