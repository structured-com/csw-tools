# csw-tools

A collection of automation utilities for Cisco Secure Workload (CSW, formerly
Tetration), available through a single command-line package.

## Current utilities

These core utilities set up `csw-tools` for general use:

| Command | Description | Documentation |
|---|---|---|
| `init` | Create or replace the per-user `config.toml` | [Guide](src/csw_tools/commands/init/README.md) |
| `configure-credentials` | Inspect or replace CSW API credentials in the OS keyring | [Guide](src/csw_tools/commands/configure_credentials/README.md) |

The following command is implemented for production use:

| Command | Description | Documentation |
|---|---|---|
| `create-scopes` | Create scopes in bulk from CSV | [Guide](src/csw_tools/commands/create_scopes/README.md) |

The following commands are in DEV/TESTING state and generally should not be
used in production yet:

| Command | Description | Documentation |
|---|---|---|
| `clean-stale-labels` | Remove old static labels for workloads absent from current inventory | [Guide](src/csw_tools/commands/clean_stale_labels/README.md) |
| `convert-labels` | Persist observed inventory fields as per-workload static labels | [Guide](src/csw_tools/commands/convert_labels/README.md) |
| `prune-agents` | Preview and destructively decommission stale agents and clean explicit related objects | [Guide](src/csw_tools/commands/prune_agents/README.md) |
| `prune-policy` | Not implemented: remove or filter entries within a workspace policy | [Guide](src/csw_tools/commands/prune_policy/README.md) |
| `sync-collection-rules` | Not implemented: validate collection rules against scope and filter IPs | [Guide](src/csw_tools/commands/sync_collection_rules/README.md) |

## Installation

The project requires Python 3.12 or later. [uv](https://docs.astral.sh/uv/) is
the preferred installation and development tool, although normal Python package
installation also works.

Once uv is installed, install the package as a tool:

```console
uv tool install csw-tools
```

## Usage

List the available commands, common options, and project version information:

```console
csw-tools --help
csw-tools --version
```

Global options must appear before the command name. The command and its own
options follow:

```console
csw-tools create-scopes scopes.csv
csw-tools --dashboard my-company.tetrationcloud.com prune-agents
csw-tools -d my-company configure-credentials
```

Every command's guide is linked from the tables above. The command help is the
authoritative option list:

```console
csw-tools COMMAND --help
```

## Dashboard selection

A dashboard may be supplied as its short SaaS name, full
`tetrationcloud.com` hostname, or HTTPS URL:

```console
csw-tools -d my-company COMMAND
csw-tools -d my-company.tetrationcloud.com COMMAND
csw-tools -d https://my-company.tetrationcloud.com COMMAND
```

On-premises instances require an explicit HTTPS origin, such as
`-d https://csw.example.org` or `-d https://192.0.2.10`. Paths, credentials
embedded in URLs, and custom ports are rejected.

TLS certificate verification is enabled by default. Use
`--no-dashboard-verify-tls` only for a trusted deployment whose certificate
cannot be validated normally.

## Configuration

Settings are resolved in this order:

1. CLI arguments
2. `config.toml` values
3. Backend defaults from `config_defaults.py`
4. Interactive prompt for an unresolved required value

`config.toml` is optional but useful for values that are reused often. Create
or replace it interactively with:

```console
csw-tools init
```

This copies the packaged
[`config.example.toml`](src/csw_tools/config.example.toml) to the OS-native
per-user location:

- macOS: `~/Library/Application Support/csw-tools/config.toml`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/csw-tools/config.toml`
- Windows: `%APPDATA%\csw-tools\config.toml`

Use `--config PATH` before the command name to select a different file. If the
target already exists, `init` asks before replacing it and defaults to keeping
the existing file.

## Credentials

Secrets are stored through the operating system keyring and are never written
to `config.toml` or a backup. For a dashboard named `my-company`, the default
identifiers are:

| Service name | Username | Stored value |
|---|---|---|
| `csw-tools:my-company` | `csw:api_key` | CSW API key |
| `csw-tools:my-company` | `csw:api_secret` | CSW API secret |

Inspect or replace the credential pair interactively:

```console
csw-tools -d my-company configure-credentials
```

On-premises credentials use the normalized HTTPS origin as the dashboard
suffix. Configure them using the same URL supplied to other commands.

Use the least-privileged API key that provides the capabilities listed in the
selected command's guide.

## Safe change workflow

Mutating commands default to `--dry-run`; review the complete plan before using
`--apply`. Supported commands create a timestamped JSON backup before the first
API mutation and update it around each attempted operation. Keep backups until
the results have been validated.

Rollback support and limitations differ by command. Read the individual command
guide before applying or recovering changes. Agent decommissioning has no true
rollback.

## Development

See [DEV-GUIDELINES.md](DEV-GUIDELINES.md) for architecture, shared APIs,
contributor workflow, and project checks.
