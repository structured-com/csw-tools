"""Placeholder for the prune-agents utility."""

import click

from csw_tools.context import AppContext, pass_app_context


@click.command("prune-agents")
@pass_app_context
def command(app: AppContext) -> None:
    """Run the prune-agents utility (not implemented yet)."""

    app.error_console.print(
        "[yellow]The 'prune-agents' command is not implemented yet.[/yellow]"
    )
    raise click.exceptions.Exit(1)
