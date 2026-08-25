# csw-tools

A collection of automation utilities for Cisco Secure Workload (CSW, formerly Tetration), available
through this single package.

## Current utilities

The command suite currently includes:

| Command | Description |
|---|---|
| `init` | Creates/replaces a per-user configuration file (config.toml) |
| `configure-credentials` | (WIP) Creates/replaces API credentials in the OS keyring |
| `prune-agents` | (WIP) Review and remove stale agents and related objects |
| `prune-policy` | (WIP) Easily remove/filter entries within a workspace policy |
| `sync-collection-rules` | (WIP) Validate Collection Rules are synced with all scope/filter defined IP's |


## Installation

[uv](https://docs.astral.sh/uv/) is the preferred tool for installing and
developing `csw-tools`, but other methods (`pip`, etc) will work just as normally

Once UV is installed in your system, install package as a tool:

```console
uv tool install csw-tools
```

## Usage

List the available commands and common options:

```console
csw-tools --help
csw-tools --version
```

Common options (e.g. CSW dashboard name) must appear before the command name, if using. Then the main command can be invoked. See various examples below:

```console
csw-tools prune-policy
csw-tools --dashboard my-company.tetrationcloud.com prune-agents
csw-tools -d my-company configure-credentials
```


## Configuration

Configuration settings are resolved in this order:

1. CLI arguments
2. `config.toml` values
3. Backend default from `config_defaults.py`
4. Interactive prompt for required unresolved values

`config.toml` is optional but can be useful for setting config values one-time that are reused often.

To use `config.toml`, simply run:

```console
csw-tools init
```

This will copy the packaged [`config.example.toml`](src/csw_tools/config.example.toml) to the OS-native
per-user configuration location::

- macOS: `~/Library/Application Support/csw-tools/config.toml`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/csw-tools/config.toml`
- Windows: `%APPDATA%\csw-tools\config.toml`



## Credentials

Secrets are stored through the OS's keyring and will never be put
in `config.toml`. Using keyring's terminology, CSW credentials have these
identifiers, in example:

| Service name | Username | Password |
|---|---|---|
| `csw-tools:my-company` | `csw:api_key` | The actual CSW API key |
| `csw-tools:my-company` | `csw:api_secret` | The actual CSW API secret |


Inspect and replace the credential pair interactively with:

```console
csw-tools --dashboard my-company configure-credentials
```


## Project Organization

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


