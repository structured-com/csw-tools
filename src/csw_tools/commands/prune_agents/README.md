# `csw-tools prune-agents`

Status: DEV/TESTING.

> **Destructive:** Agent decommissioning has no true rollback. A backup of agent
> details cannot re-register an agent or restore historical telemetry. Begin
> with a dry run and review every proposed action and manual-review notice.

The command finds stale software agents, plans their decommissioning, and
conservatively cleans explicit related filters, policies, and static-label
records when ownership can be proven.

## Prerequisites and API visibility

Use an appropriately authorized administrative API key with
`sensor_management`, `flow_inventory_query`, `app_policy_management`, and
`user_data_upload`, with access to the intended agents, inventory, filters, and
workspaces. Some policy APIs also require `user_role_scope_management`.

Incomplete visibility cannot prove IP ownership and makes automatic related
cleanup unsafe. Run against a release whose documented OpenAPI behavior has
been validated for these resources.

## Usage

```console
csw-tools -d my-company prune-agents --dry-run
csw-tools -d my-company prune-agents --lastdate 1788220800 --dry-run
csw-tools -d my-company prune-agents --apply
csw-tools -d my-company prune-agents --apply --noconfirm
```

| Option | Meaning |
|---|---|
| `--lastdate EPOCH` | Inclusive stale cutoff in UTC epoch seconds |
| `--noconfirm` | Skip individual prompts but still display the complete plan |
| `--apply` / `--dry-run` | Apply destructive changes or preview; default dry-run |
| `--page-size INTEGER` | Agents, inventory, and policies requested per page; default 500 |
| `--rollback BACKUP` | Limited recovery of related objects; requires `--apply` |

Global options such as `--output-dir`, `--dashboard`, and `--config` must
precede the command. `page_size` and other command option names can be set in
the `[prune-agents]` configuration table. Journals use
`[common].output_dir`, whose default is `csw-tools-outputs`.

## Staleness

`--lastdate EPOCH` is an inclusive cutoff in Unix epoch **seconds**, in UTC.
Future cutoffs are rejected. Without the option, the cutoff is the execution
time minus exactly 30 days, not a calendar month.

Staleness is based on the software-agent API's `last_config_fetch_at` value,
which is the last configuration contact. It is not based on software
installation time or label age. Missing, zero, invalid, or future contact
timestamps are never considered stale. Already decommissioned agents are not
deleted again.

## Review and confirmation

The complete plan lists agent names and UUIDs plus each related object's ID and
relationship. A policy entry identifies its consumer/provider filter, stale
interface IP, and associated agent. Long names wrap rather than disappearing.
For shared filters, the proposed narrowed query is printed.

By default, `--apply` asks about every element, with No as the default. All
decisions are collected before any API mutation. `--noconfirm` suppresses the
questions but never suppresses the plan or destructive warning, and it does not
imply `--apply`.

Non-interactive execution requires `--noconfirm` and a dashboard supplied on
the CLI or in configuration. Other invocations require an interactive terminal.

Agent contact, IP ownership/current observation, and object state are rechecked
after confirmation. A changed object is not overwritten. A declined agent
protects all of its related operations; failed or declined prerequisites protect
dependent operations. Independent actions may continue after API failures.

## Automatic cleanup boundaries

- Exact positive IP equality and membership expressions (`eq` and `in`) inside
  `and`/`or` inventory-filter expressions can be pruned. For example,
  `IP=A OR IP=B` becomes `IP=B` when only A is stale.
- Removing a required AND term makes that branch false; it never broadens the
  filter. Empty results are deleted, never replaced with an empty or match-all
  query.
- Policies whose entire consumer or provider endpoint is an eligible stale
  filter are deleted before that filter. Shared policies remain with their
  ports, action, approval flags, and priority; their targets narrow through the
  shared filter. Each policy impact receives a separate confirmation.
- Only exact per-IP, scope-independent static-label records are cleaned.
  Containing subnets are not deleted.
- Observed inventory records are displayed for manual review and are not
  deleted. The command does not use an undocumented standalone inventory-record
  deletion endpoint.
- An IP must belong to exactly one listed stale agent and be absent from current
  inventory before related-object cleanup is eligible.
- Shared or reused IPs, primary/public filters, negated/subnet expressions, and
  ambiguous objects are protected.
- General label-based membership, scopes, ADM clusters, and historical or
  enforced policy versions are not rewritten.
- Automatic policy edits cover only the latest working `v*` version in each
  visible workspace.
- No publish or enforce API is called. Editing a shared inventory filter can
  still affect existing consumers, including consumers outside the working
  policy version; review filter usage before approval.

The final summary reports deleted, updated, failed, and skipped actions. Policy
impact acknowledgements are not counted as separate updates.

## Backup and failure journal

Before any mutation, `--apply` writes a unique timestamped JSON file in
`csw-tools-outputs/`, or the selected common output directory. Its printed path
is both the pre-change backup and operation result/error journal.

The journal records original objects, planned changes, associations, user
decisions, per-operation outcomes, and errors. It is updated before and after
requests. Failure to persist it stops further writes. Protect it as sensitive
workload and policy data. The command does not retry an uncertain mutation.

## Limited recovery

```console
csw-tools -d my-company prune-agents \
  --apply --rollback csw-tools-outputs/prune-agents-TIMESTAMP.json
```

Recovery verifies the command and dashboard, displays a recovery plan, and
prompts for each element unless `--noconfirm` is supplied. It can restore
supported filter definitions, static labels, and policy definitions where
possible.

Recreated filters and policies receive new IDs. Recovered policy endpoints are
remapped, and service ports and approval flags are restored. External references
to old IDs are not repaired automatically. Updated filters are restored only if
their current state still matches the recorded post-prune value.

Recovery creates a separate pre-change journal and records completion in the
original backup to avoid duplicate recreation. A changed workspace version,
conflicting object, interrupted request, or uncertain API result requires manual
review of both journals and the dashboard before retrying.

**Agents cannot be restored**, and recovery never publishes or enforces policy.

## API compatibility

The adapter uses documented OpenAPI v1 resource paths shared by SaaS and
on-premises deployments. Unexpected response schemas or pagination fail closed.
Release-specific behavior still needs validation against the target instance.
See [Cisco's Secure Workload OpenAPI reference](https://www.cisco.com/c/en/us/td/docs/security/workload_security/secure_workload/user-guide/4_0/cisco-secure-workload-user-guide-saas-v40/m-openapi.html).

## Developer notes

This command separates its implementation into:

- [`command.py`](command.py): Click surface, cutoff, review, confirmation,
  rendering, and orchestration.
- [`api.py`](api.py): release-sensitive checked resource paths, pagination,
  identifier quoting, and single-attempt mutations.
- [`planner.py`](planner.py): staleness, ownership, query analysis, protected
  cases, operations, and pre-mutation state checks.
- [`execution.py`](execution.py): dependency-aware application, result
  verification, recovery validation, ID remapping, and recovery journaling.

Keep planning free of mutations and unknown schemas fail-closed. A successful
HTTP status alone is not enough for destructive operations when the returned
object contradicts the intended result. See
[DEV-GUIDELINES.md](../../../../DEV-GUIDELINES.md) for the shared API, Rich,
configuration, and backup contracts.
