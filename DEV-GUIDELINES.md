# csw-tools developer guidelines

This document describes the shared framework used by every command. End-user
and command-specific behavior belongs in the command README linked from the
root [README.md](README.md).

## Project layout

```text
csw-tools/
├── pyproject.toml              Project metadata, dependencies and tool settings
├── uv.lock                     Locked development dependency graph
├── .python-version             Project Python target used by uv/pyenv
├── src/csw_tools/
│   ├── cli.py                  Root Click group and command registration
│   ├── context.py              Per-invocation AppContext
│   ├── config.py               TOML loading and setting resolution
│   ├── config_defaults.py      Shared constants and backend defaults
│   ├── dashboard.py            Dashboard validation and normalization
│   ├── dashboard_context.py    Dashboard command decorator
│   ├── csw_api.py              tetpyclient construction and checked API facade
│   ├── keyring_store.py        Testable operating-system keyring adapter
│   ├── interaction.py          Interactive-terminal safeguard
│   ├── change_control.py       Backup creation, persistence and validation
│   ├── project_info.py         Diagnostic version metadata
│   └── commands/               One subpackage and README per command
└── tests/                      pytest coverage for shared and command behavior
```

`pyproject.toml` is the source of package metadata and dependency declarations.
The package version is defined there only. `.python-version` selects the project
interpreter; the packaged `project-python-version.txt` mirrors it so an installed
wheel can report the same target. A test prevents those values from drifting.

## Command entrypoint: inputs and outputs

The console entrypoint declared in `pyproject.toml` calls `csw_tools.cli:cli`.
The root Click group validates common inputs, builds one `AppContext`, and lets
Click pass that object to the selected command.

```text
CLI arguments ─┐
config.toml ────┼─> root Click group ─> AppContext ─> selected command
backend defaults┘                         │
                                          ├─ resolved settings
interactive prompt (when still needed) ──┤
OS keyring (lazy) ────────────────────────┤
                                          ├─ Common output directory
                                          ├─ Rich stdout console
                                          └─ Rich stderr console
```

Settings resolve in this order:

1. CLI value
2. Command or common `config.toml` value
3. Backend default
4. Interactive prompt when the value is required and still unresolved

Global options must precede the command. Command-specific configuration tables
become Click `default_map` entries, so Click performs the final CLI-over-config
selection for command options. A command can also inspect its validated table
with `app.config_for(command_name)`.

Normal results go to `app.console` on stdout. Warnings, progress, and diagnostic
messages go to `app.error_console` on stderr. Use Rich tables and panels through
those shared consoles; do not create new consoles inside a command. Expected
usage, configuration, backup, or API failures should become `click.UsageError`
or `click.ClickException` so callers receive a useful message and nonzero exit.

`[common].output_dir` and the global `--output-dir` select one shared location
for command-owned backups, journals, reports, and logs. Relative paths resolve
from the current working directory. Commands choose their own filenames and
formats but use `app.output_dir` instead of defining directory options.

CLI transcript logging is controlled by `[common].log_cli_output` and
`--log-cli-output/--no-log-cli-output`, and is enabled by default. The root group
tees application-emitted stdout and stderr to one ANSI-free text file while
preserving the original terminal streams. It suppresses entered prompt values
and remains active through Click's final handled-error rendering. Root help,
eager version output, unknown commands, and configuration failures that occur
before common settings resolve are intentionally not logged.

## Click command structure

Each command is a subpackage under `src/csw_tools/commands/` with an
`__init__.py`, `command.py`, and `README.md`. Export its Click command from the
subpackage, import it in `cli.py`, and register it with `cli.add_command(...)`.

A dashboard-backed interactive command normally follows this shape:

```python
@click.command("example")
@click.option("--value")
@pass_app_context
@interactive_command
@dashboard_command
def command(app: AppContext, value: str | None) -> None: ...
```

Decorator order is significant. `pass_app_context` supplies the shared object,
`interactive_command` rejects redirected/non-terminal stdin before prompting,
and `dashboard_command` resolves the dashboard, activates dashboard-scoped
services, and prints the dashboard panel. A deliberately non-interactive mode
requires a command-specific guard and explicit safety design.

Click owns option parsing and help. Keep the command docstring useful because it
is the authoritative `COMMAND --help` text. Rich owns presentation after inputs
are valid. Never let formatting truncate identifiers needed to review a change.

## Shared context and configuration

`AppContext` contains validated configuration, the selected config path, the
base keyring service name, normalized dashboard state, TLS verification, and
the two Rich consoles. It contains a keyring accessor, not plaintext secrets.

`config.py` validates known TOML sections and common value types. Unknown
sections and common keys fail early rather than being silently ignored. The
removed command-level `backup_dir` setting fails with a migration message rather
than being ignored.
`config_defaults.py` is for true backend defaults and fixed identifiers. Add a
command name and config section there when its options may be configured.

The default user config is OS-native. An explicit `--config` path must exist for
normal commands; the implicit default is optional. Commands should receive
resolved values from Click/AppContext rather than reparsing TOML themselves.

## Dashboard, credentials, and CSW API

`dashboard.py` accepts a SaaS short name, any valid ASCII FQDN, or an HTTPS
origin. A short name expands under `tetrationcloud.com`; a dotted non-SaaS FQDN
normalizes to the same URL-based identity as its explicit HTTPS form. IP
addresses and ambiguous single-label on-premises hosts require the explicit
URL. Normalization rejects user information, paths, queries, fragments, and
custom ports so API endpoints and keyring namespaces are unambiguous.

`dashboard_command` prompts only if no dashboard was supplied, calls
`AppContext.activate_dashboard()`, and displays the canonical selection. The
activation appends the dashboard name to the base keyring service, isolating
credentials between organizations. Its Rich panel shows a display name and the
canonical URL using markup-safe text objects.

`create_api_client()` retrieves the fixed API-key and secret usernames lazily
from `KeyringStore` and constructs `tetpyclient.RestClient` with the normalized
HTTPS endpoint and configured TLS verification. Never write credentials to
configuration, logs, backups, exception text, or Rich output.

`CswApi` is the checked facade around `tetpyclient`. It centralizes request
encoding, HTTP status checks, JSON decoding, common CSW operations, and inventory
pagination. Unexpected response shapes, repeated offsets, or invalid JSON fail
closed with `CswRequestError`. Put shared, stable operations here; keep
release-specific or command-only adapters inside that command's package.

Use only the API permissions a command needs and document them in its README.
Keep TLS verification enabled unless the user explicitly disables it for a
trusted deployment.

## Rich, interaction, and change control

Use Rich for human-reviewable panels, tables, warnings, and summaries. Preserve
full object identifiers and identifying name tails. Markup must be disabled for
untrusted dashboard/API text when it could be interpreted as Rich markup.

Most commands use `interactive_command` because their workflows can prompt for
missing settings or approvals. Help and the eager `--version` option must remain
usable without a terminal and before configuration or dashboard resolution.

Mutating commands should default to dry-run and use the helpers in
`commands/common.py` and `change_control.py`. Create the backup before the first
mutation, journal attempted and completed operations atomically, verify command
and dashboard identity during recovery, and stop further mutations if the
journal cannot be persisted. Write those artifacts beneath `app.output_dir` and
define recovery limitations in the command README.

## Core setup commands

### `init`

The root CLI uses valid existing common output settings for `init`, but falls
back to CLI/backend defaults when the existing configuration is missing or
invalid so it can still be replaced. The command receives the resolved target
through `AppContext.config_path`, reads the packaged example with
`importlib.resources`, writes beside the target, and atomically replaces it only
after confirmation. After any successful create, replace, or retain path, it
offers to open the containing directory through the native desktop launcher.
Launcher errors are warnings and do not turn successful initialization into a
failure. It does not activate a dashboard or keyring.

### `configure-credentials`

This command uses the normal interactive and dashboard decorators. Dashboard
activation constructs the scoped keyring service before the command checks the
two fixed usernames. The stored API key is partially masked, and the API secret
is never displayed. New-value prompts hide each one, collect it once, and report
only its character count. Keyring errors must never include secrets and should
warn that separate writes may leave an incomplete pair.

See the command READMEs for complete user-facing details:

- [`init`](src/csw_tools/commands/init/README.md)
- [`configure-credentials`](src/csw_tools/commands/configure_credentials/README.md)

## Adding or changing a command

1. Create or update its command subpackage and README.
2. Define Click arguments/options and their backend defaults without duplicating
   global configuration or dashboard setup.
3. Register new config sections/constants and the command in the root CLI.
4. Use shared context, API, output, interaction, and backup helpers where their
   contracts apply.
5. Document inputs, required API capabilities, dry-run/apply semantics, failure
   behavior, recovery limits, and command-specific architecture.
6. Add unit tests for parsing/planning and CLI tests for help, success, failure,
   output, interactive safety, API validation, and partial mutation handling.

Do not put command-specific implementation notes in this file. Link to the
command README instead.

## Development workflow

Install the Python 3.12 locked environment and run the CLI:

```console
uv sync
uv run csw-tools --help
uv run csw-tools --version
```

Run all checks before submitting changes:

```console
uv run ruff format --check .
uv run ruff check .
uv run pytest
uv lock --check
uv build
```

`uv build` uses the `uv_build` backend configured in `pyproject.toml`. Test the
built wheel from outside the checkout when changing packaged resources,
entrypoints, or version reporting.
