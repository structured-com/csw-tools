# `csw-tools clean-stale-labels`

Status: DEV/TESTING.

Compares scope-independent static workload-label records with current CSW
inventory and removes eligible stale records.

## Prerequisites

Select the intended dashboard and configure an API key with
`flow_inventory_query` and `user_data_upload` access. The key must be able to
see the complete relevant inventory; incomplete visibility can make an active
address appear absent.

This command requires an interactive terminal. It defaults to `--dry-run`.

## Eligibility rules

A static-label record is eligible only when both conditions are true:

1. No currently observed inventory IP belongs to the label record's IP address
   or subnet.
2. The record's `updatedAt` timestamp is at least `--minimum-age DAYS` old.

The default minimum age is 30 days. Numeric epoch timestamps in seconds or
milliseconds and ISO timestamps are accepted. Records without a usable
`updatedAt` value are reported and skipped.

Deletion removes the entire static-label record and all of its attributes for
that IP address or subnet; it does not remove individual keys from the record.

## Usage

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

| Option | Meaning |
|---|---|
| `--minimum-age DAYS` | Required age before deletion; default 30 |
| `--ip-range CIDR` | Limit static-label discovery; repeat as needed |
| `--page-size INTEGER` | Current inventory records per API page; default 500 |
| `--apply` / `--dry-run` | Delete eligible records or preview; default dry-run |
| `--rollback BACKUP` | Recreate deleted records; requires `--apply` |

`minimum_age` and `page_size` can be set in the `[clean-stale-labels]`
`config.toml` table. Backups use `[common].output_dir`, whose default is
`csw-tools-outputs`. Global options such as `--output-dir` must precede the
command.

## Plan, backup, and rollback

The plan displays each eligible IP/subnet, its timestamp, and its complete
attributes. It separately reports records skipped because their timestamp could
not be interpreted. A dry run makes no API mutations.

`--apply` writes every original attribute map to a timestamped JSON backup
before the first deletion and updates that file around each attempt. Rollback
recreates the deleted records from those saved attributes:

```console
csw-tools -d my-company clean-stale-labels \
  --apply --rollback csw-tools-outputs/clean-stale-labels-TIMESTAMP.json
```

The backup must have been produced by this command for the same normalized
dashboard. Keep it until the resulting inventory labels have been validated.

## Developer notes

[`command.py`](command.py) normalizes observed IPs with `ipaddress`, deduplicates
records returned by overlapping search ranges, and tests address membership
within same-family networks. `plan_cleanup()` is side-effect free apart from API
reads; deletion begins only after planning and backup creation.

Use the shared API and change-control contracts in
[DEV-GUIDELINES.md](../../../../DEV-GUIDELINES.md). Preserve the fail-safe rule
that an unparseable timestamp is never eligible.
