# `csw-tools init`

Status: core utility.

Creates or replaces the optional per-user `config.toml`. This command does not
select a dashboard, read credentials, or call the CSW API.

## Usage

Create the configuration at the OS-native location:

```console
csw-tools init
```

Select another location with the global option before the command:

```console
csw-tools --config ./config.toml init
```

The native locations are:

- macOS: `~/Library/Application Support/csw-tools/config.toml`
- Linux: `${XDG_CONFIG_HOME:-~/.config}/csw-tools/config.toml`
- Windows: `%APPDATA%\csw-tools\config.toml`

## Behavior

The new file is copied from the packaged
[`config.example.toml`](../../config.example.toml). If the target exists,
`init` asks whether to overwrite it and defaults to No. It reports the full
target path and then points to `configure-credentials` as the next setup step.
After creating, replacing, or retaining the file, it asks whether to open the
containing directory and defaults to No. If accepted, it uses Finder on macOS,
Explorer on Windows, or the default `xdg-open` handler on Linux. A launcher
failure produces a warning without changing the successful initialization
result.

The command refuses to replace a directory. It also reports a Click error if
the parent directory or file cannot be written. An existing malformed TOML file
can still be replaced because `init` deliberately does not parse it first.

`init` requires an interactive terminal because replacement confirmation may
be needed.

## Developer notes

The root CLI creates an empty `AppConfig` when `init` is selected, bypassing the
normal configuration loader while still resolving common backend defaults. The
selected target is passed in `AppContext.config_path`.

[`command.py`](command.py) loads the example with `importlib.resources`, writes
it to a temporary file in the target directory, and atomically replaces the
destination. This prevents a partial configuration file if writing fails. The
native directory launch uses platform-specific commands without a shell.

See the root [README](../../../../README.md) for general setup and
[DEV-GUIDELINES.md](../../../../DEV-GUIDELINES.md) for the shared command flow.
