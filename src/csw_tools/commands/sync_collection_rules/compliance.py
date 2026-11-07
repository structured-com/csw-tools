"""Parse manual labels and check coverage without enumerating addresses."""

from __future__ import annotations

import csv
import io
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from ipaddress import IPv4Network, IPv6Network, collapse_addresses, ip_network

IPNetwork = IPv4Network | IPv6Network


@dataclass(frozen=True)
class LabelEntry:
    network: IPNetwork
    rows: tuple[int, ...]

    @property
    def display(self) -> str:
        if self.network.prefixlen == self.network.max_prefixlen:
            return str(self.network.network_address)
        return str(self.network)


@dataclass(frozen=True)
class Finding:
    entry: LabelEntry
    issue: str


def parse_network(value: object, source: str) -> IPNetwork:
    if not isinstance(value, str) or not value.strip() or "%" in value:
        raise ValueError(f"{source}: expected an IPv4/IPv6 address or CIDR subnet")
    try:
        return ip_network(value.strip(), strict=False)
    except ValueError as exc:
        raise ValueError(
            f"{source}: invalid IPv4/IPv6 address or CIDR subnet {value!r}"
        ) from exc


def parse_labels_csv(text: str) -> list[LabelEntry]:
    """Group equivalent first-column IPs, preserving CSV source line numbers."""

    reader = csv.reader(
        io.StringIO(text.removeprefix("\ufeff"), newline=""), strict=True
    )
    entries: dict[IPNetwork, list[int]] = {}
    try:
        header = next(reader, None)
        if not header or header[0] != "IP":
            raise ValueError("Manual-label CSV must have 'IP' as its first header")
        while True:
            line_number = reader.line_num + 1
            row = next(reader, None)
            if row is None:
                break
            if not row or all(not cell.strip() for cell in row):
                continue
            network = parse_network(row[0], f"Manual-label CSV row {line_number}")
            entries.setdefault(network, []).append(line_number)
    except csv.Error as exc:
        raise ValueError(
            f"Malformed manual-label CSV near row {reader.line_num}: {exc}"
        ) from exc
    return [LabelEntry(network, tuple(rows)) for network, rows in entries.items()]


def parse_include_networks(payload: object) -> list[IPNetwork]:
    """Check the complete response; only INCLUDE rules contribute coverage."""

    if not isinstance(payload, list):
        raise ValueError("Collection Rules response must be a list of rule objects")
    includes: list[IPNetwork] = []
    for index, rule in enumerate(payload, start=1):
        source = f"Collection Rule {index}"
        if not isinstance(rule, dict):
            raise ValueError(f"{source}: expected a rule object")
        if rule.get("action") not in ("INCLUDE", "EXCLUDE"):
            raise ValueError(f"{source}: action must be INCLUDE or EXCLUDE")
        network = parse_network(rule.get("subnet"), source)
        if rule["action"] == "INCLUDE":
            includes.append(network)
    return includes


def check_compliance(
    entries: Iterable[LabelEntry], includes: Iterable[IPNetwork]
) -> list[Finding]:
    """Find absent/partial union coverage using merged ranges and binary search."""

    families: dict[int, list[IPNetwork]] = {4: [], 6: []}
    for network in includes:
        families[network.version].append(network)
    merged = {
        version: list(collapse_addresses(networks))
        for version, networks in families.items()
    }
    starts = {
        version: [int(network.network_address) for network in networks]
        for version, networks in merged.items()
    }
    findings: list[Finding] = []
    for entry in entries:
        network = entry.network
        networks = merged[network.version]
        family_starts = starts[network.version]
        first, last = int(network.network_address), int(network.broadcast_address)
        index = bisect_right(family_starts, first) - 1
        previous_end = int(networks[index].broadcast_address) if index >= 0 else -1
        if previous_end >= last:
            continue
        overlaps = previous_end >= first or (
            index + 1 < len(networks) and family_starts[index + 1] <= last
        )
        issue = "Partial INCLUDE coverage" if overlaps else "No INCLUDE coverage"
        findings.append(Finding(entry, issue))
    return findings
