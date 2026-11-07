# `csw-tools sync-collection-rules`

Status: DEV/TESTING. Phase 1 provides a read-only compliance report.

The command checks whether IP addresses and subnets in manual user labels have
complete coverage from Collection Rules with action `INCLUDE`. Missing coverage
can prevent flow-learnt inventory and labels from being available to inventory
search and policy discovery. Public and tunnel IPs are particularly relevant.

```console
csw-tools -d my-company sync-collection-rules
csw-tools -d my-company sync-collection-rules --dry-run
csw-tools -d my-company sync-collection-rules --root-app-scope-name Production --vrf-name Default
```

## Options and configuration

| Option | Behavior |
|---|---|
| `--apply` / `--dry-run` | Defaults to dry-run. Phase 1 rejects `--apply` before accessing credentials or calling APIs. |
| `--root-app-scope-name NAME` | Root scope for manual-label download; defaults to the dashboard name. |
| `--vrf-name NAME` | VRF for Collection Rules; defaults independently to the dashboard name. |

For SaaS dashboards, the default name is the normalized tenant name, such as
`my-company`, even when the dashboard is supplied as an FQDN or HTTPS URL. For
other dashboards, it is the hostname/IP shown in the dashboard panel, without
`https://`. Explicit names preserve case and may contain spaces; quote them in
the shell. Empty names and the path segments `.` and `..` are rejected.

Settings resolve from CLI options, then `[sync-collection-rules]` in `config.toml`,
then backend/dashboard defaults:

```toml
[sync-collection-rules]
apply = false
# root_app_scope_name = "Production"
# vrf_name = "Default"
```

`--dry-run` can override `apply = true` in an existing configuration. There is
no rollback option, mutation, or backup in phase 1. Execution requires an
interactive terminal, following the shared command convention. `-h`/`--help`
works without a terminal, dashboard, or credentials.

## APIs and access

The command makes these scoped GET requests:

- `/openapi/v1/assets/cmdb/download/{root_app_scope_name}` returns manual labels
  as CSV. The unscoped download is not used.
- `/openapi/v1/collection_rules/{vrf_name}` returns the ordered list of rules.

Names are encoded as individual URL path segments. Existing dashboard-scoped
keyring credentials and TLS verification settings are reused. Per Cisco's
[Secure Workload 4.0 OpenAPI reference](https://www.cisco.com/c/en/us/td/docs/security/workload_security/secure-workload/user-guide/4_0/cisco-secure-workload-user-guide-on-prem-v40/secure-workload-openapis.html#concept_292656),
Collection Rules APIs require the `flow_inventory_query` capability and site
administrator access. User-defined label APIs require `user_data_upload`;
scoped label GET APIs allow users with read access to the root scope.

## Compliance and report behavior

The CSV must be UTF-8 (an initial BOM is accepted), with `IP` as the first
header. Only the first column contributes addresses; wholly blank rows are
ignored. IPv4 and IPv6 addresses/CIDRs are accepted. CIDRs containing host bits
are normalized to their network, such as `10.9.9.9/8` becoming `10.0.0.0/8`.
Equivalent entries are grouped, retaining their starting CSV line numbers.

Python's standard-library `ipaddress` module merges INCLUDE networks separately
for IPv4 and IPv6. A label passes when **all** its addresses have INCLUDE
coverage, including coverage assembled from multiple rules. Two adjacent /25s
therefore cover their containing /24. Addresses are never individually expanded;
merged ranges and binary search keep large subnets practical.

**Phase 1 evaluates INCLUDE coverage only. EXCLUDE rules and rule precedence
are not evaluated.** A passing result does not establish the effective outcome
of the ordered rules when INCLUDE and EXCLUDE entries overlap.

The Rich findings table contains only entries needing action:

| IP/subnet | CSV rows | Issue |
|---|---|---|
| `192.0.2.10` | 2, 8 | No INCLUDE coverage |
| `198.51.100.0/24` | 4 | Partial INCLUDE coverage |

A summary gives the number of unique entries checked, covered, and needing
action. A fully covered input produces a short success message without a findings
table. Header-only CSV produces `No manual-label IPs found.` Empty rule lists or
lists containing only EXCLUDE rules leave all labeled entries needing action.

A completed report exits **0**, including reports with missing or partial
coverage. Invalid input, credentials, or API responses exit nonzero and do not
produce a completed compliance summary. Invalid CSV addresses identify their
source row; malformed rule objects identify their rule number. HTTP failures
report status without exposing response bodies or credentials.

Normal shared transcript logging saves output under the common output directory
(`csw-tools-outputs/` by default); use the global `--no-log-cli-output` to disable
it. No additional report files or backups are created.

## Implementation

`command.py` owns options and Rich presentation. `api.py` wraps the two scoped
read operations using the shared checked `CswApi`; `compliance.py` owns CSV/rule
parsing, normalization, deduplication, and coverage calculations. No writes to
Secure Workload are implemented.
