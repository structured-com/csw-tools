"""Root command-line interface for csw-tools."""

from __future__ import annotations

from pathlib import Path

import click
from rich.console import Console

from csw_tools import config as config_module
from csw_tools.commands.configure_credentials import (
    command as configure_credentials_command,
)
from csw_tools.commands.init import command as init_command
from csw_tools.commands.prune_agents import command as prune_agents_command
from csw_tools.commands.prune_policy import command as prune_policy_command
from csw_tools.commands.sync_collection_rules import (
    command as sync_collection_rules_command,
)
from csw_tools.config import (
    AppConfig,
    ConfigError,
    load_config,
    resolve_setting,
)
from csw_tools.config_defaults import (
    DEFAULT_DASHBOARD_VERIFY_TLS,
    DEFAULT_KEYRING_SERVICE_NAME,
)
from csw_tools.context import AppContext
from csw_tools.dashboard import DASHBOARD, Dashboard
from csw_tools.keyring_store import KeyringStore


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config",
    "config_path",
    type=click.Path(path_type=Path, dir_okay=False),
    help=(
        "Use an alternate config file location instead of the per-user "
        "default (config.toml)."
    ),
)
@click.option(
    "--keyring-service-name",
    help="Override the default keyring service name (of 'csw-tools')",
)
@click.option(
    "-d",
    "--dashboard",
    type=DASHBOARD,
    help="Use this Secure Workload dashboard (name, FQDN, or HTTPS URL).",
)
@click.option(
    "--dashboard-verify-tls/--no-dashboard-verify-tls",
    default=None,
    help="Enable or disable TLS certificate verification for dashboard APIs.",
)
@click.version_option(package_name="csw-tools")
@click.pass_context
def cli(
    ctx: click.Context,
    config_path: Path | None,
    keyring_service_name: str | None,
    dashboard: Dashboard | None,
    dashboard_verify_tls: bool | None,
) -> None:
    """A collection of automation utilities for Cisco Secure Workload (CSW)"""

    try:
        selected_config_path = (
            (
                config_path
                if config_path is not None
                else config_module.default_config_path()
            )
            .expanduser()
            .resolve()
        )
        if ctx.invoked_subcommand == "init":
            config = AppConfig()
        else:
            config = load_config(
                selected_config_path,
                explicit=config_path is not None,
            )
        resolved_service_name = resolve_setting(
            name="keyring service name",
            cli_value=keyring_service_name,
            config_value=config.common.keyring_service_name,
            default=DEFAULT_KEYRING_SERVICE_NAME,
        )
        if not resolved_service_name or not resolved_service_name.strip():
            raise ConfigError("The keyring service name cannot be empty")
        resolved_service_name = resolved_service_name.strip()
        resolved_dashboard = resolve_setting(
            name="CSW dashboard",
            cli_value=dashboard,
            config_value=config.common.dashboard,
        )
        resolved_dashboard_verify_tls = resolve_setting(
            name="dashboard TLS verification",
            cli_value=dashboard_verify_tls,
            config_value=config.common.dashboard_verify_tls,
            default=DEFAULT_DASHBOARD_VERIFY_TLS,
        )
        if resolved_dashboard_verify_tls is None:
            raise ConfigError("Dashboard TLS verification could not be resolved")
    except ConfigError as exc:
        raise click.ClickException(str(exc)) from exc

    ctx.default_map = config.as_click_default_map()
    ctx.obj = AppContext(
        config=config,
        config_path=selected_config_path,
        keyring_service_name=resolved_service_name,
        dashboard=resolved_dashboard,
        dashboard_verify_tls=resolved_dashboard_verify_tls,
        console=Console(),
        error_console=Console(stderr=True),
        _keyring_factory=KeyringStore,
    )


cli.add_command(configure_credentials_command)
cli.add_command(init_command)
cli.add_command(prune_agents_command)
cli.add_command(prune_policy_command)
cli.add_command(sync_collection_rules_command)
