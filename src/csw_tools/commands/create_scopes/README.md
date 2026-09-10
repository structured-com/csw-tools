# `csw-tools create-scopes`

Status: implemented.

Validates and creates CSW scopes in bulk from a UTF-8 CSV file. An existing
scope with the same fully qualified name is skipped.

## Prerequisites

Select the intended dashboard and configure an API key with
`user_role_scope_management` access to the parent scopes and scope hierarchy.
The command requires an interactive terminal and defaults to `--dry-run`.

## CSV input

The header is:

```csv
short_name,parent,description,query,filter_json,policy_priority
```

| Column | Required | Description |
|---|---|---|
| `short_name` | Yes | Local name of the new scope |
| `parent` | Yes | Exact, case-sensitive, fully qualified parent scope |
| `description` | No | Scope description |
| `query` | No | Friendly filter expression |
| `filter_json` | No | Raw CSW `short_query` JSON for advanced filters |
| `policy_priority` | No | Integer policy priority |

Unknown columns are rejected. Parent scopes must already exist or appear earlier
in the CSV than their children. A row may provide `query` or `filter_json`,
but not both. When both are blank or omitted, the command sends the CSW
no-filter query `{"type":"none"}`, not JSON `null`.

## Friendly scope queries

The `query` column supports `=`, `!=`, `EQ`, `NE`, `IN`, `CONTAINS`,
`REGEX`, `AND`, `OR`, `NOT`, and parentheses. Operator precedence is
`NOT`, then `AND`, then `OR`; parentheses can make grouping explicit.
Labels shown with `*` in the CSW interface are translated to their OpenAPI
names, so `*Env` becomes `user_Env`.

For example:

```text
*Env = Prod AND *App IN (App1, App2)
```

is equivalent to:

```text
(*Env = Prod AND *App = App1) OR (*Env = Prod AND *App = App2)
```

and generates:

```json
{
  "type": "and",
  "filters": [
    {"type": "eq", "field": "user_Env", "value": "Prod"},
    {"type": "in", "field": "user_App", "values": ["App1", "App2"]}
  ]
}
```

Quote values containing spaces, commas, parentheses, or operator words. CSV
requires embedded double quotes to be doubled. The dry-run plan displays both
the friendly input and generated `short_query`:

```csv
short_name,parent,description,query,filter_json,policy_priority
ProductionApps,Tetration,Production applications,"*Env = Prod AND *App IN (App1, App2)",,100
Shared,Tetration,Shared services,"*Owner = ""Shared Services""",,
Empty,Tetration,Scope with no query,,,
```

For a CSW filter outside the friendly grammar, leave `query` blank and provide
`filter_json`. Double the JSON quote characters for CSV:

```csv
short_name,parent,description,query,filter_json,policy_priority
Legacy,Tetration,Legacy filter,,"{""type"":""eq"",""field"":""user_Env"",""value"":""Prod""}",100
```

## Usage

Preview and apply a file:

```console
csw-tools -d my-company create-scopes scopes.csv --dry-run
csw-tools -d my-company create-scopes scopes.csv --apply
```

| Option or argument | Meaning |
|---|---|
| `CSV_FILE` | Input file; required unless rolling back |
| `--backup-dir DIRECTORY` | Backup and error-log location; default `csw-tools-backups` |
| `--apply` / `--dry-run` | Create scopes or validate and preview; default dry-run |
| `--rollback BACKUP` | Delete scopes created by a prior run; requires `--apply` |

Global options such as `--dashboard` and `--config` must precede the command.

## Batch results and errors

Every row is validated independently. Invalid queries, priorities, missing
parents, duplicate names, and CSW API failures include the CSV line number.
Independent later rows continue processing. A child whose parent failed is
reported as its own failure because the parent is unavailable.

When rows fail, a uniquely named
`create-scopes-errors-TIMESTAMP.log` is written in `--backup-dir`. It records
the input path, failed line, full scope name, and error. The command exits
nonzero after processing the complete batch so automation can detect partial
failure.

Every run prints created, failed, and skipped totals. Dry runs also print how
many scopes would be created. Long fully qualified names retain their
identifying tail with a leading ellipsis, for example:

```text
...LongApp:App1
```

## Backup and rollback

`--apply` writes a timestamped JSON backup before the first creation and
updates it after every attempt. A partial run therefore records which scopes
were created and which failed.

Rollback deletes only scopes created by that run, in child-first order:

```console
csw-tools -d my-company create-scopes \
  --apply --rollback csw-tools-backups/create-scopes-TIMESTAMP.json
```

If CSW created a scope but execution was interrupted before its ID was saved,
rollback can rediscover it by exact fully qualified name. The backup must match
this command and the active normalized dashboard.

## Developer notes

[`command.py`](command.py) contains a deliberately small tokenizer and
recursive-descent parser for the friendly query language, followed by separate
CSV reading, hierarchy validation, planning, rendering, application, and
rollback functions. Preserve boolean precedence and the no-filter
`{"type":"none"}` contract when extending the grammar.

Batch application journals each API attempt and rewrites the failure log as row
results change. Continue independent rows after a failure, but never treat a
failed planned parent as available to its children. Shared API, output, and
backup conventions are documented in
[DEV-GUIDELINES.md](../../../../DEV-GUIDELINES.md).
