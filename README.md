# csw-tools

A collection of automation utilities for Cisco Secure Workload (CSW, formerly
Tetration), available through a single command-line package.

## Commands

| Command | Description |
|---|---|
| `clean-stale-labels` | Remove old static labels for workloads absent from current inventory |
| `configure-credentials` | Inspect or replace CSW API credentials in the OS keyring |
| `convert-labels` | Persist observed inventory fields as per-workload static labels |
| `create-scopes` | Create scopes in bulk from CSV |
| `init` | Create or replace the per-user `config.toml` |
| `prune-agents` | Preview and destructively decommission stale agents; clean explicit related filters, policies and static labels |
| `prune-policy` | WIP: remove or filter entries within a workspace policy |
| `sync-collection-rules` | WIP: validate collection rules against scope and filter IPs |

## Requirements and installation

The project requires Python 3.12 or later. [uv](https://docs.astral.sh/uv/) is
the preferred installation and development tool, although normal Python package
installation also works.

Install the CLI with uv:

```console
uv tool install csw-tools
```

For development from a checkout:

```console
uv sync
uv run csw-tools --help
```

## Dashboard and credentials

Global options must appear before the command name. A dashboard can be supplied
as its short name, full `tetrationcloud.com` hostname, or HTTPS URL:

```console
csw-tools --dashboard my-company.tetrationcloud.com COMMAND
csw-tools -d my-company COMMAND
```

On-premises instances require an explicit HTTPS origin, for example
`csw-tools -d https://csw.example.org COMMAND` or `-d https://192.0.2.10`.
Paths, credentials embedded in URLs, and custom ports are rejected. Existing
SaaS names and keyring entries are unchanged. On-premises credentials use the
normalized HTTPS origin as the dashboard-specific keyring suffix; configure
them using that same URL with `configure-credentials`.

Credentials are stored in the operating system keyring and are never written to
`config.toml` or a backup. Configure them interactively:

```console
csw-tools -d my-company configure-credentials
```

The default keyring entries are:

| Service name | Username | Stored value |
|---|---|---|
| `csw-tools:my-company` | `csw:api_key` | CSW API key |
| `csw-tools:my-company` | `csw:api_secret` | CSW API secret |

The API key needs capabilities appropriate to the command:

- Label conversion and cleanup require `flow_inventory_query` plus
  `user_data_upload` access.
- Bulk scope creation requires `user_role_scope_management` access.
- Agent pruning requires `sensor_management`, `flow_inventory_query`,
  `app_policy_management`, and `user_data_upload`, with access to the intended
  agents, inventory, filters and workspaces. Some policy APIs also require
  `user_role_scope_management`. Incomplete visibility cannot prove IP ownership;
  use an appropriately authorized administrative account.
- Use the least-privileged API key that provides the required operations.

TLS certificate verification is enabled by default. Use
`--no-dashboard-verify-tls` only when connecting to a trusted deployment whose
certificate cannot be validated normally.

## Configuration

Settings are resolved in this order:

1. CLI arguments
2. `config.toml` values
3. Backend defaults from `config_defaults.py`
4. Interactive prompt for an unresolved required value

Create the optional per-user configuration file with:

```console
csw-tools init
```

The file is copied from
[`src/csw_tools/config.example.toml`](src/csw_tools/config.example.toml) to the
native user configuration location:

- macOS: `~/Library/Application Support/csw-tools/config.toml`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/csw-tools/config.toml`
- Windows: `%APPDATA%\csw-tools\config.toml`

Use `--config PATH` before the command name to select a different file.

## Safe change workflow

The label and scope commands use the following safeguards. Agent pruning uses
the same dry-run and backup defaults, but has the destructive recovery
limitations documented below.

- The default is `--dry-run`; it displays the plan without changing CSW.
- `--apply` explicitly enables changes.
- Before the first API mutation, `--apply` creates a timestamped JSON backup in
  `csw-tools-backups/` by default.
- The backup is updated around each attempted API operation so a partially
  completed run remains recoverable.
- `--backup-dir DIRECTORY` selects another backup location.
- `--apply --rollback BACKUP.json` reverses the attempted operations recorded in
  a backup after verifying its command and dashboard.

Review every dry run before applying it. Keep backups until the resulting
inventory labels and scope hierarchy have been validated.

## Convert dynamic labels to static labels

`convert-labels` reads fields from currently observed inventory records and
persists their values as static labels on each workload IP. Existing static
labels are preserved; only requested target keys are added or updated.

Specify each field with a repeatable `--label SOURCE[:TARGET]` option:

- `--label hostname` copies `hostname` to the static label `hostname`.
- `--label user_environment` reads `user_environment` and writes `environment`.
- `--label aws_name:asset_name` reads `aws_name` and writes `asset_name`.

Preview and apply a conversion:

```console
csw-tools -d my-company convert-labels \
  --label hostname:asset_name \
  --label os \
  --dry-run

csw-tools -d my-company convert-labels \
  --label hostname:asset_name \
  --label os \
  --apply
```

Limit inventory to an exact fully qualified implied scope with `--scope`, and
control inventory pagination with `--page-size`:

```console
csw-tools -d my-company convert-labels \
  --scope Tetration:Production:Web \
  --label hostname \
  --page-size 500
```

Rollback restores every prior label map and removes label records that were
created by the original run:

```console
csw-tools -d my-company convert-labels \
  --apply --rollback csw-tools-backups/convert-labels-TIMESTAMP.json
```

## Create scopes in bulk

`create-scopes` validates and creates scopes from a UTF-8 CSV file. It skips an
existing scope with the same fully qualified name.

The header is:

```csv
short_name,parent,description,query,filter_json,policy_priority
```

| Column | Required | Description |
|---|---|---|
| `short_name` | Yes | Local name of the new scope |
| `parent` | Yes | Exact, case-sensitive, fully qualified parent scope name |
| `description` | No | Scope description |
| `query` | No | Friendly filter expression described below |
| `filter_json` | No | Raw CSW `short_query` JSON for advanced filters |
| `policy_priority` | No | Integer policy priority |

Parent scopes must already exist or appear earlier in the CSV than their
children. A row may provide `query` or `filter_json`, but not both. When both
fields are blank or omitted, the command sends the CSW no-filter query
`{"type":"none"}` so a scope with no query can be created without sending JSON
`null`.

### Friendly scope queries

The `query` column supports `=`, `!=`, `EQ`, `NE`, `IN`, `CONTAINS`, `REGEX`,
`AND`, `OR`, `NOT`, and parentheses. Operator precedence is `NOT`, then `AND`,
then `OR`; parentheses can make the intended grouping explicit. Labels shown
with `*` in the CSW interface are translated to their OpenAPI names, so `*Env`
becomes `user_Env`.

For example, this expression:

```text
*Env = Prod AND *App IN (App1, App2)
```

is equivalent to:

```text
(*Env = Prod AND *App = App1) OR (*Env = Prod AND *App = App2)
```

and generates this CSW `short_query`:

```json
{
  "type": "and",
  "filters": [
    {"type": "eq", "field": "user_Env", "value": "Prod"},
    {"type": "in", "field": "user_App", "values": ["App1", "App2"]}
  ]
}
```

Quote values containing spaces, commas, parentheses, or operator words. As
required by CSV, embedded double quotes are doubled. The dry-run plan displays
both the friendly input and the generated `short_query`:

```csv
short_name,parent,description,query,filter_json,policy_priority
ProductionApps,Tetration,Production applications,"*Env = Prod AND *App IN (App1, App2)",,100
Shared,Tetration,Shared services,"*Owner = ""Shared Services""",,
Empty,Tetration,Scope with no query,,,
```

For a CSW filter not covered by the friendly syntax, leave `query` blank and
provide `filter_json`. Double the JSON quote characters according to CSV
escaping rules:

```csv
short_name,parent,description,query,filter_json,policy_priority
Legacy,Tetration,Legacy filter,,"{""type"":""eq"",""field"":""user_Env"",""value"":""Prod""}",100
```

### Batch results and error handling

The command treats each CSV row independently. Invalid queries, invalid
priorities, missing parents, and CSW API failures include the row's CSV line
number and do not prevent later independent rows from being processed. A child
whose parent failed is reported as its own failure because that parent is
unavailable.

When any rows fail, a uniquely named
`create-scopes-errors-TIMESTAMP.log` file is written in `--backup-dir`
(default: `csw-tools-backups`). It records each failed line, full scope name,
and error. The command exits nonzero after processing the complete batch so
automation can detect partial failure.

Every run prints created, failed, and skipped totals. Dry runs also print the
number of scopes that would be created. Long fully qualified scope names in the
plan retain their identifying tail with a leading ellipsis, for example:

```text
...LongApp:App1
```

Preview, apply, and rollback:

```console
csw-tools -d my-company create-scopes scopes.csv --dry-run
csw-tools -d my-company create-scopes scopes.csv --apply
csw-tools -d my-company create-scopes \
  --apply --rollback csw-tools-backups/create-scopes-TIMESTAMP.json
```

Rollback deletes only scopes created by that run and processes them in
child-first order. If a run was interrupted after CSW created a scope but before
its ID was saved, rollback can rediscover it by its exact fully qualified name.

## Clean up labels for stale workloads

`clean-stale-labels` compares scope-independent static workload label records
with current CSW inventory. A label record is eligible for deletion only when:

1. No currently observed inventory IP belongs to the label's IP address or
   subnet.
2. The label record's `updatedAt` timestamp is at least `--minimum-age DAYS` old.

The default threshold is 30 days. Records without a usable `updatedAt` timestamp
are reported and skipped. Deletion removes the entire static label record for
the IP address or subnet.

Preview with the default threshold, then apply a 60-day threshold:

```console
csw-tools -d my-company clean-stale-labels --dry-run
csw-tools -d my-company clean-stale-labels --minimum-age 60 --apply
```

By default the command examines all IPv4 and IPv6 static labels. Use repeatable
`--ip-range CIDR` options to limit discovery:

```console
csw-tools -d my-company clean-stale-labels \
  --ip-range 10.0.0.0/8 \
  --ip-range 2001:db8::/32 \
  --dry-run
```

Rollback recreates the deleted label records from their saved attributes:

```console
csw-tools -d my-company clean-stale-labels \
  --apply --rollback csw-tools-backups/clean-stale-labels-TIMESTAMP.json
```

Run the command help for the authoritative option list and defaults:

```console
csw-tools convert-labels --help
csw-tools create-scopes --help
csw-tools clean-stale-labels --help
csw-tools prune-agents --help
```

## Prune stale agents and related objects

**Destructive: agent decommissioning has no true rollback.** A backup of agent
details cannot re-register the agent or restore historical telemetry. Begin with
a preview and review both the actions and the manual-review notices.

```console
csw-tools -d my-company prune-agents --dry-run
csw-tools -d my-company prune-agents --lastdate 1788220800 --dry-run
csw-tools -d my-company prune-agents --apply
csw-tools -d my-company prune-agents --apply --noconfirm
```

`--lastdate EPOCH` is an inclusive cutoff in Unix epoch **seconds**, in UTC.
Without it, the cutoff is the execution time minus **30 days**, not a calendar
month. Staleness is based on the software-agent API's `last_config_fetch_at`
(last configuration contact), not software installation time or label age.
Unknown, zero, invalid, or future contact timestamps are never considered stale.
Already decommissioned agents are not deleted again.

The complete plan lists agent names/UUIDs and each related object's ID and
relationship. For example, a policy entry identifies its consumer/provider
filter, the stale interface IP, and the associated agent. Long names wrap rather
than disappearing. For shared filters, the proposed query is printed as well.

By default, `--apply` asks about **every element**, with No as the default answer;
all decisions are collected before any API mutations. `--noconfirm` suppresses
these questions but never suppresses the plan or warning, and does not imply
`--apply`. Non-interactive execution requires `--noconfirm` and a dashboard
specified on the CLI or in configuration. Other commands keep their existing
interactive behavior.

### Cleanup boundaries

- Exact positive IP equality and membership (`eq`/`in`) inside `and`/`or`
  inventory-filter expressions are pruned. For example, `IP=A OR IP=B` becomes
  `IP=B` when only A is stale. Removing a required AND term makes that entire
  branch false; it never broadens the filter. Empty results are deleted, never
  replaced with `{}` or a match-all query.
- Policies whose entire consumer or provider endpoint is an eligible stale
  filter are deleted before that filter. Shared policies remain in place with
  their existing ports, action and priority; their targets are narrowed through
  the shared filter. Each such policy impact receives a separate confirmation.
- Exact per-IP **scope-independent static label records** are cleaned up.
  Containing subnets are not deleted. Observed inventory records are displayed
  for manual review, not directly deleted: this implementation does not use an
  undocumented standalone inventory-record deletion endpoint.
- An IP must belong to exactly one listed stale agent and be absent from current
  inventory to qualify for related-object cleanup. Shared/reused IPs, primary or
  public filters, negated/subnet expressions, and ambiguous objects are protected.
  General label-based membership, scopes, ADM clusters, and historical/enforced
  policy versions are not rewritten. Automatic policy edits cover only the
  latest working `v*` version in each visible workspace.
- No publish or enforce API is called. Editing a shared inventory filter can
  nevertheless affect its existing consumers, including outside the working
  policy version. Review the filter's usage before approving its change.

Agent contact, IP ownership/current observation, and object state are rechecked
after confirmation. A changed object is not overwritten. A declined agent
protects its related operations; failed or declined prerequisites protect
dependent operations. Independent actions can continue after API failures.
The final summary reports deleted, updated, failed and skipped actions. Policy
impact acknowledgements are not counted as separate updates.

### Backups, failure reporting and limited recovery

Before any mutation, `--apply` writes a unique timestamped JSON file in
`csw-tools-backups/` (override with `--backup-dir DIRECTORY`). Its printed path is
both the **backup and result/error journal**. It records the original objects,
planned changes, associations, decisions, per-operation outcomes and errors.
Failure to persist the journal stops further writes. Protect these files as
sensitive workload/policy data. The command does not retry uncertain mutations.

```console
csw-tools -d my-company prune-agents --apply --rollback csw-tools-backups/prune-agents-TIMESTAMP.json
```

Recovery checks the command and dashboard, displays the recovery plan and
prompts individually unless `--noconfirm` is supplied. It restores supported
filter definitions, static labels and policies where possible. Recreated
filters/policies receive new IDs; recovered policy endpoints are remapped, and
service ports and approval flags are restored. External references to old IDs
are not repaired automatically. Updated filters are restored only if their
current state still matches the recorded post-prune value.

Recovery creates a separate pre-change journal and records completion in the
original backup to avoid duplicate recreations. A changed workspace version,
conflicting object, interrupted request or uncertain API result requires manual
review; inspect the journals and dashboard before retrying. **Agents cannot be
restored**, and recovery never publishes or enforces policies.

The adapter uses the documented OpenAPI v1 resource paths shared by SaaS and
on-premises deployments. Unexpected response schemas or pagination fail closed;
release-specific behavior still needs validation against your instance. See
[Cisco's Secure Workload OpenAPI reference](https://www.cisco.com/c/en/us/td/docs/security/workload_security/secure_workload/user-guide/4_0/cisco-secure-workload-user-guide-saas-v40/m-openapi.html).

## Project organization

Shared CLI context, dashboard normalization, API construction, configuration,
Rich output, keyring access, and backup handling live in `src/csw_tools/`. Each
utility has a dedicated subpackage under `src/csw_tools/commands/`.

## Development checks

Run the complete project checks before submitting changes:

```console
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv lock --check
uv build
```
