# `csw-tools configure-credentials`

Status: core utility.

Inspects whether the CSW API key and secret are configured for one dashboard
and optionally replaces the pair in the operating system keyring. The API key
is shown only as a masked preview, and the secret is never displayed.

## Usage

```console
csw-tools -d my-company configure-credentials
csw-tools -d csw.example.org configure-credentials
csw-tools -d https://csw.example.org configure-credentials
```

The global `--dashboard`/`-d`, `--config`, `--keyring-service-name`, and TLS
options must appear before the command. If no dashboard is supplied through the
CLI or configuration, the command prompts for one.

## Keyring entries

For the SaaS dashboard `my-company`, the default entries are:

| Service name | Username | Stored value |
|---|---|---|
| `csw-tools:my-company` | `csw:api_key` | CSW API key |
| `csw-tools:my-company` | `csw:api_secret` | CSW API secret |

On-premises credentials use the normalized HTTPS origin as the dashboard
suffix. The base service name defaults to `csw-tools` and can be overridden as
an advanced global setting.

## Interaction and safety

The command displays the first and last three characters of a configured API
key with the middle masked. Keys of six characters or fewer are masked in full.
The secret is reported only as `(configured, hidden)`; missing values are
identified clearly. It then asks whether to store a new pair. No is the default
and leaves the keyring unchanged. Each new value is entered once through a
hidden prompt, followed by a character count so pasted input can be checked.

If keyring access fails, the error excludes all secret values. Because the key
and secret are stored separately, a backend failure during the second write may
leave the pair incomplete; rerun the command to inspect and replace it.

This command requires an interactive terminal. It stores credentials but does
not validate them with an API request; the selected operational command reports
authentication or authorization failures.

## Developer notes

The shared decorators first inject `AppContext`, reject non-interactive use,
then resolve and activate the dashboard. Activation constructs the
dashboard-specific `KeyringStore`; secrets remain lazy and are read only inside
the command.

Fixed keyring usernames live in `config_defaults.py`. [`command.py`](command.py)
uses the shared store adapter so tests can supply an in-memory replacement
without accessing the host keyring.

See the root [README](../../../../README.md) for general setup and
[DEV-GUIDELINES.md](../../../../DEV-GUIDELINES.md) for the shared credential and
dashboard architecture.
