"""Interactive CSW API credential configuration workflow."""

from __future__ import annotations

import click

from csw_tools.config_defaults import (
    CSW_API_KEY_USERNAME,
    CSW_API_SECRET_USERNAME,
)
from csw_tools.context import AppContext, pass_app_context
from csw_tools.interaction import interactive_command
from csw_tools.keyring_store import KeyringStoreError


def _credential_status(value: str | None) -> str:
    """Return a display-only status without exposing a credential value."""

    return "[green]configured[/green]" if value else "[yellow]missing[/yellow]"


@click.command("configure-credentials")
@pass_app_context
@interactive_command
def command(app: AppContext) -> None:
    """Inspect and replace the CSW API credential pair."""

    try:
        api_key = app.keyring.get_password(CSW_API_KEY_USERNAME)
        api_secret = app.keyring.get_password(CSW_API_SECRET_USERNAME)
    except KeyringStoreError as exc:
        raise click.ClickException(str(exc)) from exc

    app.console.print(f"CSW API key: {_credential_status(api_key)}")
    app.console.print(f"CSW API secret: {_credential_status(api_secret)}")

    if not click.confirm("Configure a new CSW API key and secret?", default=False):
        app.console.print("No credentials were changed.", style="yellow")
        return

    new_api_key = click.prompt(
        "CSW API key",
        hide_input=True,
        confirmation_prompt=True,
    )
    new_api_secret = click.prompt(
        "CSW API secret",
        hide_input=True,
        confirmation_prompt=True,
    )

    try:
        app.keyring.set_password(CSW_API_KEY_USERNAME, new_api_key)
        app.keyring.set_password(CSW_API_SECRET_USERNAME, new_api_secret)
    except KeyringStoreError as exc:
        raise click.ClickException(
            f"{exc}. Credential storage may be incomplete; rerun "
            "'csw-tools configure-credentials'."
        ) from exc

    app.console.print(
        f"Stored CSW API credentials in keyring service '{app.keyring.service_name}'.",
        style="green",
        markup=False,
    )
