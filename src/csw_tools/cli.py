"""Root command-line interface for csw-tools."""

from __future__ import annotations

import tomllib
from pathlib import Path

import click
from rich.console import Console

from csw_tools import config as config_module
from csw_tools.commands.clean_stale_labels import (
    command as clean_stale_labels_command,
)
from csw_tools.commands.configure_credentials import (
    command as configure_credentials_command,
)
from csw_tools.commands.convert_labels import command as convert_labels_command
from csw_tools.commands.create_scopes import command as create_scopes_command
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
from csw_tools.project_info import format_version_info


class FullCommandHelpGroup(click.Group):
    """Wrap command summaries instead of shortening them with ellipses."""

    def format_commands(
        self, ctx: click.Context, formatter: click.HelpFormatter
    ) -> None:
        rows = []
        for name in self.list_commands(ctx):
            command = self.get_command(ctx, name)
            if command is None or command.hidden:
                continue
            # Preserve Click's summary and deprecation handling, but allow the
            # complete summary through to the formatter's word wrapping.
            summary = command.get_short_help_str(limit=len(command.help or ""))
            rows.append((name, summary))
        if rows:
            with formatter.section("Commands"):
                formatter.write_dl(rows)


def show_version(ctx: click.Context, _parameter: click.Parameter, value: bool) -> None:
    """Print project and interpreter information, then exit."""

    if not value or ctx.resilient_parsing:
        return
    try:
        output = format_version_info()
    except (OSError, KeyError, RuntimeError, tomllib.TOMLDecodeError) as exc:
        raise click.ClickException(
            f"Could not load project version information: {exc}"
        ) from exc
    click.echo(output)
    ctx.exit()


@click.group(
    cls=FullCommandHelpGroup,
    context_settings={"help_option_names": ["-h", "--help"]},
)
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
    help="Use this Secure Workload dashboard (SaaS name, FQDN, or HTTPS origin).",
)
@click.option(
    "--dashboard-verify-tls/--no-dashboard-verify-tls",
    default=None,
    help="Enable or disable TLS certificate verification for dashboard APIs.",
)
@click.option(
    "--version",
    is_flag=True,
    is_eager=True,
    expose_value=False,
    callback=show_version,
    help="Show project and Python version information, then exit.",
)
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
cli.add_command(clean_stale_labels_command)
cli.add_command(convert_labels_command)
cli.add_command(create_scopes_command)
cli.add_command(init_command)
cli.add_command(prune_agents_command)
cli.add_command(prune_policy_command)
cli.add_command(sync_collection_rules_command)
