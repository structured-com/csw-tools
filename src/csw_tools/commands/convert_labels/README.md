# `csw-tools convert-labels`

Status: DEV/TESTING.

Reads fields from currently observed CSW inventory and persists their values as
scope-independent static labels on each workload IP. Existing static labels are
preserved; only requested target keys are added or updated.

## Prerequisites

Select the intended dashboard and configure an API key with
`flow_inventory_query` and `user_data_upload` access. Use the least-privileged
key that can read the intended inventory and update its static labels.

This command requires an interactive terminal. It defaults to `--dry-run`.

## Label mappings

Specify each inventory field with a repeatable `--label SOURCE[:TARGET]`:

- `--label hostname` copies `hostname` to the static label `hostname`.
- `--label user_environment` reads `user_environment` and writes
  `environment`; an unmapped leading `user_` is removed.
- `--label aws_name:asset_name` reads `aws_name` and writes `asset_name`.

Empty or missing source values are skipped. At least one `--label` is required
unless a rollback is being performed.

## Usage

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

Limit inventory to an exact, fully qualified implied scope and control API
pagination:

```console
csw-tools -d my-company convert-labels \
  --scope Tetration:Production:Web \
  --label hostname \
  --page-size 500
```

Global options such as `--dashboard` and `--config` must precede the command.
The command-specific options are:

| Option | Meaning |
|---|---|
| `--label SOURCE[:TARGET]` | Field to persist; repeat for multiple mappings |
| `--scope NAME` | Exact fully qualified implied scope used for inventory search |
| `--page-size INTEGER` | Inventory records requested per API page; default 500 |
| `--apply` / `--dry-run` | Apply changes or display the plan; default dry-run |
| `--rollback BACKUP` | Restore an earlier run; requires `--apply` |

These options may also be supplied through the `[convert-labels]` table in
`config.toml`; repeatable labels use a TOML list such as
`labels = ["hostname", "os"]`. Backups use `[common].output_dir`, whose
default is `csw-tools-outputs`; the global `--output-dir` option must precede
the command.

## Plan, backup, and rollback

The plan displays each workload IP, its prior static-label map, and its proposed
map. Workloads with no usable requested values or no resulting change are
omitted. A dry run makes no API mutations.

`--apply` creates a timestamped JSON backup in `csw-tools-outputs/` by default
before the first update. It marks each operation attempted and applied around
the API request so a partially completed run remains recoverable. Keep this file
until the labels have been validated.

Rollback restores every prior label map and removes label records created by the
original run:

```console
csw-tools -d my-company convert-labels \
  --apply --rollback csw-tools-outputs/convert-labels-TIMESTAMP.json
```

The backup must have been produced by `convert-labels` for the same normalized
dashboard. Rollback processes attempted operations in reverse order.

## Developer notes

[`command.py`](command.py) parses mappings, requests only the IP and selected
inventory dimensions, reads each existing static-label record, and produces
complete before/after maps. API construction, dashboard activation, pagination,
backup validation, and atomic backup writes use shared modules documented in
[DEV-GUIDELINES.md](../../../../DEV-GUIDELINES.md).

Planning is separate from mutation and can be unit tested with a fake `CswApi`.
Preserve that boundary when changing mapping behavior.
