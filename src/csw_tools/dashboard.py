"""Normalize Cisco Secure Workload dashboard identifiers."""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

import click

from csw_tools.config_defaults import TETRATION_CLOUD_DOMAIN


class DashboardError(ValueError):
    """Raised when a dashboard identifier cannot be normalized safely."""


@dataclass(frozen=True, slots=True)
class Dashboard:
    """Canonical forms of one hosted Secure Workload dashboard."""

    name: str
    fqdn: str
    url: str


_DASHBOARD_NAME_PATTERN = re.compile(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\Z")
_DASHBOARD_FORMAT_MESSAGE = "use a SaaS dashboard name, a valid FQDN, or an HTTPS URL"


def normalize_dashboard(value: str) -> Dashboard:
    """Return canonical dashboard forms from a supported user input."""

    candidate = value.strip().lower()
    if not candidate:
        raise DashboardError(f"Dashboard cannot be empty; {_DASHBOARD_FORMAT_MESSAGE}")
    if any(character.isspace() for character in candidate):
        raise DashboardError("Dashboard cannot contain whitespace")

    is_url = "://" in candidate
    if is_url:
        try:
            parsed = urlsplit(candidate)
            hostname = parsed.hostname
        except ValueError as exc:
            raise DashboardError("Invalid dashboard URL") from exc
        if parsed.scheme != "https":
            raise DashboardError("Dashboard URL must use HTTPS")
        if not parsed.netloc or hostname is None:
            raise DashboardError(f"Invalid dashboard URL; {_DASHBOARD_FORMAT_MESSAGE}")
        if parsed.username is not None or parsed.password is not None:
            raise DashboardError("Dashboard URL cannot contain user information")
        try:
            port = parsed.port
        except ValueError as exc:
            raise DashboardError("Dashboard URL contains an invalid port") from exc
        if port is not None:
            raise DashboardError("Dashboard URL cannot contain a port")
        if parsed.path not in ("", "/"):
            raise DashboardError("Dashboard URL cannot contain a path")
        if "?" in candidate or parsed.query:
            raise DashboardError("Dashboard URL cannot contain a query")
        if "#" in candidate or parsed.fragment:
            raise DashboardError("Dashboard URL cannot contain a fragment")
        host = hostname.lower()
    else:
        if any(character in candidate for character in "/?#@:"):
            raise DashboardError(f"Invalid dashboard; {_DASHBOARD_FORMAT_MESSAGE}")
        host = candidate

    fqdn_suffix = f".{TETRATION_CLOUD_DOMAIN}"
    if host == TETRATION_CLOUD_DOMAIN:
        raise DashboardError("Dashboard FQDN must include a tenant hostname")
    if host.endswith(fqdn_suffix):
        name = host.removesuffix(fqdn_suffix)
    elif not is_url and "." not in host:
        name = host
    else:
        name = None

    if name is not None:
        if not _DASHBOARD_NAME_PATTERN.fullmatch(name):
            raise DashboardError(
                "Dashboard name must be one DNS label of 1-63 lowercase letters, "
                "numbers, or interior hyphens"
            )

        fqdn = f"{name}.{TETRATION_CLOUD_DOMAIN}"
        return Dashboard(name=name, fqdn=fqdn, url=f"https://{fqdn}")

    # A dotted bare hostname is unambiguously a non-SaaS dashboard. Normalize it
    # to the same URL-based identity already used by explicit on-premises URLs.
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if len(host) > 253 or not all(
            _DASHBOARD_NAME_PATTERN.fullmatch(label) for label in host.split(".")
        ):
            raise DashboardError("Invalid on-premises dashboard hostname") from None
        origin = host
    else:
        if not is_url:
            raise DashboardError(
                "Bare IP addresses are not supported; use an explicit HTTPS URL"
            )
        host = str(address)
        origin = f"[{host}]" if address.version == 6 else host

    return Dashboard(name=f"https://{origin}", fqdn=host, url=f"https://{origin}")


class DashboardParamType(click.ParamType):
    """Click parameter type that produces a normalized dashboard."""

    name = "dashboard"

    def convert(
        self,
        value: object,
        param: click.Parameter | None,
        ctx: click.Context | None,
    ) -> Dashboard | None:
        if value is None:
            return None
        if isinstance(value, Dashboard):
            return value
        if not isinstance(value, str):
            self.fail("Dashboard must be a string", param, ctx)

        try:
            return normalize_dashboard(value)
        except DashboardError as exc:
            self.fail(str(exc), param, ctx)


DASHBOARD = DashboardParamType()
