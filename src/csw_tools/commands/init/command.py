"""Placeholder for the future init workflow."""

import click

from csw_tools.context import AppContext, pass_app_context


@click.command("init")
@pass_app_context
def command(app: AppContext) -> None:
    """Initialize configuration and credentials (not implemented yet)."""

    app.error_console.print(
        "[yellow]The 'init' command is not implemented yet; "
        "no changes were made.[/yellow]"
    )
    raise click.exceptions.Exit(1)
