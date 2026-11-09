# `csw-tools sync-collection-rules`

Status: Production.

The command checks whether IP addresses and subnets in manual user labels have
complete effective coverage from ordered Collection Rules with action `INCLUDE`.
It previews required additions by default; `--apply` prepends them and uploads the
complete ordered list while preserving every existing rule. Missing coverage
can prevent flow-learnt inventory and labels from being available to inventory
search and policy discovery. Public and tunnel IPs are particularly relevant.

```console
csw-tools -d my-company sync-collection-rules
csw-tools -d my-company sync-collection-rules --dry-run
csw-tools -d my-company sync-collection-rules --apply
csw-tools -d my-company sync-collection-rules --root-app-scope-name Production --vrf-name Default
```

## Options and configuration

| Option | Behavior |
|---|---|
| `--apply` / `--dry-run` | Defaults to dry-run. Apply prepends required INCLUDE rules through one full-list POST. |
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
no rollback option or additional apply confirmation prompt. Execution requires an
interactive terminal, following the shared command convention. `-h`/`--help`
works without a terminal, dashboard, or credentials.

## APIs and access

The command makes these scoped requests:

- `/openapi/v1/assets/cmdb/download/{root_app_scope_name}` returns manual labels
  as CSV. The unscoped download is not used.
- `/openapi/v1/collection_rules/{vrf_name}` returns the ordered list of rules.
- Apply sends one POST to `/openapi/v1/collection_rules/{vrf_name}` with the
  complete replacement array, not just the additions. HTTP 2xx with
  `{"success": true}` confirms success. A returned ordered rule list is also
  accepted when its subnet/action sequence matches the submitted sequence;
  equivalent network notation is allowed. Missing, false, or non-boolean
  `success` values and other response shapes are rejected. GET still requires
  an ordered rule list.

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

The **first matching rule wins** for each address. A higher-priority EXCLUDE
blocks a later INCLUDE, even if that INCLUDE is more specific. Conversely, an
earlier INCLUDE takes precedence over a later EXCLUDE. Unmatched addresses lack
INCLUDE coverage. IPv4 and IPv6 are evaluated separately.

A label passes when **all** its addresses have effective INCLUDE coverage,
including coverage assembled from multiple rules. Two adjacent included /25s
therefore cover their containing /24. The calculation sweeps rule boundaries
and searches merged included ranges; it never expands addresses individually,
so even IPv6 /0 networks remain practical.

The Rich findings table contains only entries needing action:

| IP/subnet | CSV rows | Issue | Prepend INCLUDE |
|---|---|---|---|
| `192.0.2.10` | 2, 8 | No INCLUDE coverage | `192.0.2.10/32` |
| `198.51.100.0/24` | 4 | Partial INCLUDE coverage | `198.51.100.0/24` |

Issues describe effective coverage after evaluating EXCLUDE precedence, rather
than merely the presence of an overlapping INCLUDE rule. Each unique finding
produces one full normalized labeled subnet as an INCLUDE addition. Hosts use
`/32` or `/128`. Additions follow first appearance in the CSV; adjacent or
contained additions are neither merged nor removed.

A summary gives the number of unique entries checked, covered, and needing
action. A fully covered input produces a short success message without a findings
table. Header-only CSV produces `No manual-label IPs found.` Empty rule lists or
lists containing only EXCLUDE rules leave all labeled entries needing action.

A completed dry-run report exits **0**, including reports with missing or partial
coverage or blocked apply ordering. Invalid input, credentials, or GET responses
exit nonzero without a completed compliance summary. Apply failures also exit
nonzero; any preceding compliance summary describes the original rules, not a
successful update. Invalid CSV addresses identify their
source row; malformed rule objects identify their rule number. HTTP failures
report status without exposing response bodies or credentials.

## Apply requirements and preservation

The original GET must end with these two EXCLUDE catch-all rules, in either order:

```json
[
  {"subnet": "0.0.0.0/0", "action": "EXCLUDE"},
  {"subnet": "::/0", "action": "EXCLUDE"}
]
```

Missing or misplaced catch-alls, duplicate families in the final pair, or INCLUDE
catch-alls block apply before POST, even if no additions are needed. Correct the
ordering/actions manually in the CSW GUI. Dry-run remains available and displays
an `Apply blocked` warning for this condition.

The outgoing payload is exactly `new_includes + original_rules`. Existing
objects retain their original values and list order, including noncanonical
subnet text, additional fields, duplicates, and rules made redundant by additions.
No existing rule is normalized, removed, or moved. New top-priority INCLUDEs
override any existing EXCLUDE that blocks a labeled entry.

When no additions are needed and the final pair is valid, apply exits successfully
without POST or JSON history. Otherwise, apply sends one complete replacement
list and verifies the response. There is no automatic retry or rollback. The
GET/POST API does not provide an atomic read-modify-write guarantee; avoid
concurrent edits to this VRF while applying. Agent propagation may take time.

## Payload history and failures

Normal shared transcript logging saves output under the common output directory
(`csw-tools-outputs/` by default); use the global `--no-log-cli-output` to disable
it. The global `--output-dir` or `[common].output_dir` also controls payload
history. Disabling transcript logging does not disable apply history.

For an apply with additions, three JSON files share one UTC timestamp:

| File | Contents |
|---|---|
| `sync-collection-rules-TIMESTAMP-current.json` | Complete original GET array |
| `sync-collection-rules-TIMESTAMP-uploaded.json` | Exact array prepared for POST |
| `sync-collection-rules-TIMESTAMP.json` | Shared backup envelope with command/dashboard identity, scope, VRF, payload filenames, and operation status |

Both payload snapshots and the journal's `attempted` state are written atomically
before POST. Any write failure prevents submission; already written artifacts
are retained. The uploaded filename identifies the intended body and does not
by itself prove submission or acceptance.

After a `{"success": true}` acknowledgment or a matching ordered-list response,
the journal becomes `verified`. This records confirmation by the POST response;
no additional GET is performed. HTTP/transport failures, invalid responses, and
mismatched ordered rules leave an `unverified` outcome with a concise error.
A failed request or response check does not establish whether the server changed
its rules: inspect CSW before retrying. HTTP headers and response bodies are not
dumped into output or the journal.

If the journal cannot be updated after POST, the command exits nonzero and
explains whether the response confirmed the update or its outcome is unverified.
The saved journal still records `attempted`. An interrupted process may also
leave that state, including interruption just before submission. Preserve the
snapshots and inspect CSW before recovery. These are historical artifacts, not
input to an automated rollback command. Dry-run creates no JSON artifacts.

## Implementation

`command.py` owns options, orchestration, and Rich presentation. `api.py` wraps
scoped GET/POST operations using the shared checked `CswApi`; `compliance.py`
owns CSV/rule parsing, label normalization/deduplication, first-match coverage,
catch-all validation, and addition planning. `apply.py` saves payload history
and journals the single POST using shared change-control helpers.
