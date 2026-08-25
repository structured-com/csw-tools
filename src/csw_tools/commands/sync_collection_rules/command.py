"""Placeholder for the sync-collection-rules utility."""

import click

from csw_tools.context import AppContext, pass_app_context
from csw_tools.interaction import interactive_command


@click.command("sync-collection-rules")
@pass_app_context
@interactive_command
def command(app: AppContext) -> None:
    """Run the sync-collection-rules utility (not implemented yet)."""

    app.error_console.print(
        "[yellow]The 'sync-collection-rules' command is not implemented yet.[/yellow]"
    )
    raise click.exceptions.Exit(1)
