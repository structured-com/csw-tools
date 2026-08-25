"""Interactive configuration-file initialization workflow."""

from __future__ import annotations

from contextlib import suppress
from importlib import resources
from pathlib import Path
from tempfile import NamedTemporaryFile

import click

from csw_tools.config_defaults import CONFIG_EXAMPLE_FILENAME
from csw_tools.context import AppContext, pass_app_context
from csw_tools.interaction import interactive_command


def _copy_example_config(target_path: Path) -> None:
    """Atomically copy the packaged example configuration to ``target_path``."""

    temporary_path: Path | None = None
    try:
        contents = (
            resources.files("csw_tools").joinpath(CONFIG_EXAMPLE_FILENAME).read_bytes()
        )
        target_path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="wb",
            dir=target_path.parent,
            prefix=f".{target_path.name}.",
            delete=False,
        ) as temporary_file:
            temporary_file.write(contents)
            temporary_path = Path(temporary_file.name)
        temporary_path.replace(target_path)
    except OSError as exc:
        raise click.ClickException(
            f"Could not write configuration file: '{target_path}'"
        ) from exc
    finally:
        if temporary_path is not None:
            with suppress(OSError):
                temporary_path.unlink(missing_ok=True)


@click.command("init")
@pass_app_context
@interactive_command
def command(app: AppContext) -> None:
    """Create the csw-tools configuration file."""

    target_path = app.config_path
    existed = target_path.exists()

    if existed and not target_path.is_file():
        raise click.ClickException(f"Configuration path is not a file: '{target_path}'")

    if existed and not click.confirm(
        f"Configuration file already exists at '{target_path}'. Overwrite it?",
        default=False,
    ):
        app.console.print(
            f"Retained existing configuration: '{target_path}'",
            style="yellow",
            soft_wrap=True,
        )
    else:
        _copy_example_config(target_path)
        action = "Replaced" if existed else "Created"
        app.console.print(
            f"{action} configuration: '{target_path}'",
            style="green",
            soft_wrap=True,
        )

    app.console.print(
        "To configure CSW API credentials, next run "
        "[bold]csw-tools configure-credentials[/bold]."
    )
