"""Interactive CSW API credential configuration workflow."""

from __future__ import annotations

import click
from rich.text import Text

from csw_tools.config_defaults import (
    CSW_API_KEY_USERNAME,
    CSW_API_SECRET_USERNAME,
)
from csw_tools.context import AppContext, pass_app_context
from csw_tools.dashboard_context import dashboard_command
from csw_tools.interaction import interactive_command
from csw_tools.keyring_store import KeyringStoreError

_MASK_CHARACTER = "•"


def _masked_api_key(value: str) -> str:
    """Return a troubleshooting preview without exposing the complete API key."""

    if len(value) <= 6:
        return _MASK_CHARACTER * len(value)
    return value[:3] + (_MASK_CHARACTER * (len(value) - 6)) + value[-3:]


def _credential_line(label: str, value: str | None, *, secret: bool) -> Text:
    """Return a safely styled credential status line."""

    if not value:
        status = "missing"
        style = "yellow"
    elif secret:
        status = "(configured, hidden)"
        style = "green"
    else:
        status = _masked_api_key(value)
        style = "green"
    return Text.assemble(f"{label}: ", (status, style))


def _entered_count(label: str, value: str) -> str:
    """Describe a hidden entry by character count only."""

    unit = "character" if len(value) == 1 else "characters"
    return f"{label}: {len(value)} {unit} entered."


@click.command("configure-credentials")
@pass_app_context
@interactive_command
@dashboard_command
def command(app: AppContext) -> None:
    """Inspect and replace the CSW API credential pair."""

    try:
        api_key = app.keyring.get_password(CSW_API_KEY_USERNAME)
        api_secret = app.keyring.get_password(CSW_API_SECRET_USERNAME)
    except KeyringStoreError as exc:
        raise click.ClickException(str(exc)) from exc

    app.console.print(_credential_line("CSW API key", api_key, secret=False))
    app.console.print(_credential_line("CSW API secret", api_secret, secret=True))

    if not click.confirm("Configure a new CSW API key and secret?", default=False):
        app.console.print("No credentials were changed.", style="yellow")
        return

    new_api_key = click.prompt(
        "CSW API key",
        hide_input=True,
    )
    app.console.print(_entered_count("CSW API key", new_api_key), markup=False)
    new_api_secret = click.prompt(
        "CSW API secret",
        hide_input=True,
    )
    app.console.print(_entered_count("CSW API secret", new_api_secret), markup=False)

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
