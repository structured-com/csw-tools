"""Prepare dashboard-dependent context for an executing command."""

from __future__ import annotations

from collections.abc import Callable
from functools import wraps
from typing import cast

import click
from rich import box
from rich.panel import Panel
from rich.text import Text

from csw_tools.context import AppContext
from csw_tools.dashboard import DASHBOARD


def dashboard_command[**P, R](
    function: Callable[P, R],
) -> Callable[P, R]:
    """Resolve, activate, and display the dashboard for one command."""

    @wraps(function)
    def prepared(app: AppContext, *args: P.args, **kwargs: P.kwargs) -> R:
        dashboard = app.dashboard
        if dashboard is None:
            dashboard = click.prompt("CSW dashboard", type=DASHBOARD)

        app.activate_dashboard(dashboard)
        display_name = (
            dashboard.fqdn if dashboard.name == dashboard.url else dashboard.name
        )
        details = Text.assemble(
            ("Name: ", "bold"),
            (display_name, "bold cyan"),
            "\n",
            ("URL: ", "bold"),
            (dashboard.url, "cyan"),
        )
        app.console.print(
            Panel.fit(
                details,
                box=box.ROUNDED,
                safe_box=False,
                title="CSW Dashboard",
            )
        )
        return function(app, *args, **kwargs)

    return cast(Callable[P, R], prepared)
