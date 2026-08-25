"""Root command-line interface for csw-tools."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from csw_tools.commands.init import command as init_command
from csw_tools.commands.prune_agents import command as prune_agents_command
from csw_tools.commands.prune_policy import command as prune_policy_command
from csw_tools.commands.sync_collection_rules import (
    command as sync_collection_rules_command,
)
from csw_tools.config import ConfigError, load_config, resolve_setting
from csw_tools.config_defaults import DEFAULT_KEYRING_SERVICE_NAME
from csw_tools.context import AppContext
from csw_tools.keyring_store import KeyringStore


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help="Read configuration from PATH instead of the per-user default.",
)
@click.option(
    "--keyring-service-name",
    help="Override the keyring service name for this invocation.",
)
@click.option(
    "--no-input",
    is_flag=True,
    help="Fail instead of prompting for unresolved required values.",
)
@click.version_option(package_name="csw-tools")
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: Path | None,
    keyring_service_name: str | None,
    no_input: bool,
) -> None:
    """Run automation utilities for Cisco Secure Workload."""

    try:
        config = load_config(config_path, explicit=config_path is not None)
        resolved_service_name = resolve_setting(
            name="keyring service name",
            cli_value=keyring_service_name,
            config_value=config.common.keyring_service_name,
            default=DEFAULT_KEYRING_SERVICE_NAME,
        )
        if not resolved_service_name or not resolved_service_name.strip():
            raise ConfigError("The keyring service name cannot be empty")
        resolved_service_name = resolved_service_name.strip()
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    ctx.default_map = config.as_click_default_map()
    ctx.obj = AppContext(
        config=config,
        keyring=KeyringStore(resolved_service_name),
        console=Console(),
        error_console=Console(stderr=True),
        allow_input=not no_input,
    )


cli.add_command(init_command)
cli.add_command(prune_agents_command)
cli.add_command(prune_policy_command)
cli.add_command(sync_collection_rules_command)
