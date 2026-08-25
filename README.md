# csw-tools

A collection of automation utilities for Cisco Secure Workload (CSW), available
through a single command.

## Current utilities

The initial framework exposes these commands so their implementations can be
developed independently:

| Command | Status |
|---|---|
| `init` | Placeholder; configuration and credential setup will be added later |
| `prune-agents` | Placeholder |
| `prune-policy` | Placeholder |
| `sync-collection-rules` | Placeholder |

Placeholder commands print a clear message and exit unsuccessfully. They do not
call Cisco APIs, write configuration, or modify the system keyring.

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
csw-tools --no-input prune-agents
```

## Configuration

Copy [`config.example.toml`](config.example.toml) to the OS-native per-user
configuration location when you need file-based settings. By default, this is:

- macOS: `~/Library/Application Support/csw-tools/config.toml`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/csw-tools/config.toml`
- Windows: `%APPDATA%\csw-tools\config.toml`

Use `--config PATH` to select another file. The default file is optional; a path
given explicitly must exist and contain valid TOML.

Settings are resolved in this order:

1. Explicit CLI argument
2. `config.toml` value
3. Backend default from `config_defaults.py`
4. Interactive prompt, but only for a required value that remains unresolved
5. An actionable error when prompting is disabled or no interactive terminal is
   available

Use `--no-input` to prevent all prompting. Environment variables are not an
additional configuration source.

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
The two usernames are fixed backend values. The future `csw-tools init` workflow
will collect and store these passwords; its current placeholder performs no
writes.

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
