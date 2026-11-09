"""Parse manual labels and check coverage without enumerating addresses."""

from __future__ import annotations

import csv
import io
from bisect import bisect_right
from collections.abc import Iterable
from dataclasses import dataclass
from heapq import heappop, heappush
from ipaddress import IPv4Network, IPv6Network, ip_network
from itertools import pairwise

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


@dataclass(frozen=True)
class CollectionRule:
    network: IPNetwork
    action: str


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


def parse_collection_rules(payload: object) -> list[CollectionRule]:
    """Validate all rules without changing the original payload."""

    if not isinstance(payload, list):
        raise ValueError("Collection Rules response must be a list of rule objects")
    rules: list[CollectionRule] = []
    for index, rule in enumerate(payload, start=1):
        source = f"Collection Rule {index}"
        if not isinstance(rule, dict):
            raise ValueError(f"{source}: expected a rule object")
        if rule.get("action") not in ("INCLUDE", "EXCLUDE"):
            raise ValueError(f"{source}: action must be INCLUDE or EXCLUDE")
        network = parse_network(rule.get("subnet"), source)
        rules.append(CollectionRule(network, rule["action"]))
    return rules


def require_final_catch_alls(rules: list[CollectionRule]) -> None:
    """Reject unsafe original ordering rather than moving existing rules."""

    expected = {
        CollectionRule(ip_network("0.0.0.0/0"), "EXCLUDE"),
        CollectionRule(ip_network("::/0"), "EXCLUDE"),
    }
    if len(rules) < 2 or set(rules[-2:]) != expected:
        raise ValueError(
            "The original Collection Rules must end with EXCLUDE 0.0.0.0/0 and "
            "EXCLUDE ::/0 as the last two rules, in either order. "
            "Correct the rules manually in the CSW GUI before using --apply; "
            "csw-tools will not move or modify existing rules."
        )


def effective_include_ranges(
    rules: Iterable[CollectionRule],
) -> dict[int, list[tuple[int, int]]]:
    """Sweep rule boundaries, using the earliest active rule at each interval.

    The heap orders by original rule position, not prefix length. Only rule
    boundaries are visited, even for /0 networks; complexity is O(R log R).
    """

    boundaries: dict[int, dict[int, list[tuple[int, int, str]]]] = {4: {}, 6: {}}
    for index, rule in enumerate(rules):
        network = rule.network
        first, last = int(network.network_address), int(network.broadcast_address)
        family = boundaries[network.version]
        family.setdefault(first, []).append((index, last, rule.action))
        family.setdefault(last + 1, [])

    included: dict[int, list[tuple[int, int]]] = {4: [], 6: []}
    for version, family in boundaries.items():
        active: list[tuple[int, int, str]] = []
        points = sorted(family)
        ranges = included[version]
        for first, stop in pairwise(points):
            for candidate in family[first]:
                heappush(active, candidate)
            while active and active[0][1] < first:
                heappop(active)
            if not active or active[0][2] != "INCLUDE":
                continue
            if ranges and ranges[-1][1] + 1 == first:
                ranges[-1] = (ranges[-1][0], stop - 1)
            else:
                ranges.append((first, stop - 1))
    return included


def check_compliance(
    entries: Iterable[LabelEntry], rules: Iterable[CollectionRule]
) -> list[Finding]:
    """Find absent/partial effective INCLUDE coverage using binary search."""

    merged = effective_include_ranges(rules)
    starts = {
        version: [first for first, _last in ranges]
        for version, ranges in merged.items()
    }
    findings: list[Finding] = []
    for entry in entries:
        network = entry.network
        networks = merged[network.version]
        family_starts = starts[network.version]
        first, last = int(network.network_address), int(network.broadcast_address)
        index = bisect_right(family_starts, first) - 1
        previous_end = networks[index][1] if index >= 0 else -1
        if previous_end >= last:
            continue
        overlaps = previous_end >= first or (
            index + 1 < len(networks) and family_starts[index + 1] <= last
        )
        issue = "Partial INCLUDE coverage" if overlaps else "No INCLUDE coverage"
        findings.append(Finding(entry, issue))
    return findings


def plan_additions(findings: Iterable[Finding]) -> list[dict[str, object]]:
    """Add full labeled networks in CSV order without merging or pruning."""

    return [
        {"subnet": str(finding.entry.network), "action": "INCLUDE"}
        for finding in findings
    ]
