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
csw-tools --keyring-service-name team-csw configure-credentials
```

Commands require interactive standard input. Help and version output remain
available without an interactive terminal.

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

[prune-agents]

[prune-policy]

[sync-collection-rules]
```

Unknown sections and unknown keys under `[common]` are errors. Each utility will
validate its own settings as it is implemented.

## Credentials

Secrets are stored through the operating system's keyring and must never be put
in `config.toml`. Using keyring's terminology, CSW credentials have these
identifiers:

| Service name | Username | Password |
|---|---|---|
| `csw-tools` | `csw:api_key` | The actual CSW API key |
| `csw-tools` | `csw:api_secret` | The actual CSW API secret |

Only the service name can be changed, using
`[common].keyring_service_name` or the global `--keyring-service-name` option.
The two usernames are fixed backend values.

Inspect and replace the credential pair interactively with:

```console
csw-tools configure-credentials
```

The command reports whether each entry is configured without displaying its
value. It collects both values through hidden, confirmed prompts before writing
either entry. `init` manages only the configuration file and does not read or
write the keyring.

## Project organization

Shared CLI context, configuration loading, backend defaults, Rich output, and
keyring access live in `src/csw_tools/`. Each utility has a dedicated subpackage
under `src/csw_tools/commands/`, where contributors can add that utility's Click
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
