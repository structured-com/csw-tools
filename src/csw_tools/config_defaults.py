"""Backend defaults shared by csw-tools commands."""

APP_NAME = "csw-tools"
CONFIG_FILENAME = "config.toml"
CONFIG_EXAMPLE_FILENAME = "config.example.toml"

DEFAULT_KEYRING_SERVICE_NAME = "csw-tools"
DEFAULT_DASHBOARD_VERIFY_TLS = True
TETRATION_CLOUD_DOMAIN = "tetrationcloud.com"
CSW_API_KEY_USERNAME = "csw:api_key"
CSW_API_SECRET_USERNAME = "csw:api_secret"

COMMAND_NAMES = (
    "clean-stale-labels",
    "configure-credentials",
    "convert-labels",
    "create-scopes",
    "init",
    "prune-agents",
    "prune-policy",
    "sync-collection-rules",
)

CONFIG_SECTION_NAMES = (
    "common",
    "clean-stale-labels",
    "convert-labels",
    "create-scopes",
    "prune-agents",
    "prune-policy",
    "sync-collection-rules",
)
