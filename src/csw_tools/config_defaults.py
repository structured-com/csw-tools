"""Backend defaults shared by csw-tools commands."""

APP_NAME = "csw-tools"
CONFIG_FILENAME = "config.toml"

DEFAULT_KEYRING_SERVICE_NAME = "csw-tools"
CSW_API_KEY_USERNAME = "csw:api_key"
CSW_API_SECRET_USERNAME = "csw:api_secret"

COMMAND_NAMES = (
    "init",
    "prune-agents",
    "prune-policy",
    "sync-collection-rules",
)

CONFIG_SECTION_NAMES = (
    "common",
    "prune-agents",
    "prune-policy",
    "sync-collection-rules",
)
