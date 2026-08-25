# csw-tools

A collection of automation utilities for Cisco Secure Workload (CSW), available
through this single package.

## Current utilities

The command suite currently includes:

| Command | Status |
|---|---|
| `init` | Creates or replaces a per-user configuration file |
| `configure-credentials` | Inspects and replaces CSW API credentials in the OS keyring |
| `prune-agents` | Placeholder |
| `prune-policy` | Placeholder |
| `sync-collection-rules` | Placeholder |

The remaining placeholder commands print a clear message and exit
unsuccessfully. They do not call Cisco APIs, write configuration, or modify the
system keyring.

## Installation

[uv](https://docs.astral.sh/uv/) is the preferred tool for installing and
developing `csw-tools`.

Once the package is published, install it as a globally available command:

```console
uv tool install csw-tools
```

To install the current source checkout instead:

```console
uv tool install .
```

The project uses standard Python package metadata, so pip and pipx also work:

```console
pipx install .
# or, inside a virtual environment
pip install .
```

## Usage

List the available commands and shared options:

```console
csw-tools --help
csw-tools --version
```

Shared options must appear before the command name:

```console
csw-tools --config /path/to/config.toml prune-policy
csw-tools --dashboard my-company configure-credentials
csw-tools -d my-company --no-dashboard-verify-tls prune-agents
```

Commands require interactive standard input. Help and version output remain
available without an interactive terminal.

Every command except `init` requires a Secure Workload dashboard. Supply it
with `-d`/`--dashboard`, configure it under `[common]`, or enter it when
prompted. Before the selected command runs, a panel displays the normalized
dashboard name so it is clear which organization is active.

## Configuration

Run `init` to copy the packaged
[`config.example.toml`](src/csw_tools/config.example.toml) to the OS-native
per-user configuration location:

```console
csw-tools init
```

By default, the destination is:

- macOS: `~/Library/Application Support/csw-tools/config.toml`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/csw-tools/config.toml`
- Windows: `%APPDATA%\csw-tools\config.toml`

If the destination already exists, `init` asks before replacing it and defaults
to keeping the existing file. Use the global `--config PATH` option to
initialize an alternate location:

```console
csw-tools --config /path/to/config.toml init
```

For commands other than `init`, the default file is optional; a path given
explicitly must exist and contain valid TOML.

Settings are resolved in this order:

1. Explicit CLI argument
2. `config.toml` value
3. Backend default from `config_defaults.py`
4. Interactive prompt, but only for a required value that remains unresolved

Environment variables are not an additional configuration source.

The configuration file separates shared settings from settings owned by each
utility:

```toml
[common]
keyring_service_name = "csw-tools"
dashboard = "my-company"
dashboard_verify_tls = true

[prune-agents]

[prune-policy]

[sync-collection-rules]
```

Unknown sections and unknown keys under `[common]` are errors. Each utility will
validate its own settings as it is implemented.

The dashboard may be written in any of these equivalent forms:

```toml
dashboard = "my-company"
# dashboard = "my-company.tetrationcloud.com"
# dashboard = "https://my-company.tetrationcloud.com"
```

All forms normalize to the name `my-company`, the FQDN
`my-company.tetrationcloud.com`, and the API endpoint
`https://my-company.tetrationcloud.com`. Only hosted
`tetrationcloud.com` dashboards and HTTPS URLs are accepted. TLS certificate
verification defaults to enabled and can be overridden with the global
`--dashboard-verify-tls`/`--no-dashboard-verify-tls` option.

## Credentials

Secrets are stored through the operating system's keyring and must never be put
in `config.toml`. Using keyring's terminology, CSW credentials have these
identifiers:

| Service name | Username | Password |
|---|---|---|
| `csw-tools:my-company` | `csw:api_key` | The actual CSW API key |
| `csw-tools:my-company` | `csw:api_secret` | The actual CSW API secret |

The base service name can be changed using
`[common].keyring_service_name` or the global `--keyring-service-name` option.
The normalized dashboard name is always appended to that base, and the two
usernames are fixed backend values. Credentials previously stored under an
unsuffixed service name are not read or migrated.

Inspect and replace the credential pair interactively with:

```console
csw-tools --dashboard my-company configure-credentials
```

The command reports whether each entry is configured without displaying its
value. It collects both values through hidden, confirmed prompts before writing
either entry. `init` manages only the configuration file and does not read or
write the keyring.

Commands that use the Secure Workload API create a `tetpyclient.RestClient` on
demand. The selected endpoint and TLS setting come from the normalized
dashboard context, while the API key and secret are read lazily from the
dashboard-specific keyring service and passed directly in memory. No temporary
credentials file is created.

## Project organization

Shared CLI context, dashboard normalization, API client construction,
configuration loading, backend defaults, Rich output, and keyring access live
in `src/csw_tools/`. Each utility has a dedicated subpackage under
`src/csw_tools/commands/`, where contributors can add that utility's Click
options and business logic without expanding the root CLI module.

## Development

Install Python 3.12 and the locked development environment, then run the CLI:

```console
uv sync
uv run csw-tools --help
```

Run the project checks:

```console
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv lock --check
uv build
```

The project version is defined solely in `pyproject.toml`.
