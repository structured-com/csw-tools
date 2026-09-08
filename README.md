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
| `prune-agents` | WIP: review and remove stale agents and related objects |
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

All three implemented automation commands use the same safeguards:

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
`{"type":"none"}` so a scope with no query can be created without sending JSON `null`.

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
```

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
