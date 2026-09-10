"""Backups and rollback validation for mutating commands."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from csw_tools.dashboard import Dashboard

BACKUP_FORMAT_VERSION = 1


class BackupError(ValueError):
    """Raised when a backup cannot be safely created or consumed."""


def new_backup(
    *, command: str, dashboard: Dashboard, operations: list[dict[str, object]]
) -> dict[str, object]:
    return {
        "format_version": BACKUP_FORMAT_VERSION,
        "command": command,
        "dashboard": dashboard.name,
        "created_at": datetime.now(UTC).isoformat(),
        "operations": operations,
    }


def backup_path(directory: Path, command: str) -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    return directory.expanduser().resolve() / f"{command}-{timestamp}.json"


def write_backup(path: Path, backup: dict[str, object]) -> None:
    """Atomically write a backup, including incremental operation results."""

    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + ".tmp")
        temporary.write_text(
            json.dumps(backup, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        temporary.replace(path)
    except OSError as exc:
        raise BackupError(f"Could not write backup: {path}") from exc


def load_backup(path: Path, *, command: str, dashboard: Dashboard) -> dict[str, object]:
    try:
        backup = json.loads(path.expanduser().read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BackupError(f"Could not read backup: {path}") from exc
    if not isinstance(backup, dict):
        raise BackupError("Backup root must be a JSON object")
    if backup.get("format_version") != BACKUP_FORMAT_VERSION:
        raise BackupError("Unsupported backup format version")
    if backup.get("command") != command:
        raise BackupError(f"Backup was not created by '{command}'")
    if backup.get("dashboard") != dashboard.name:
        raise BackupError("Backup dashboard does not match the active CSW dashboard")
    if not isinstance(backup.get("operations"), list):
        raise BackupError("Backup operations must be a JSON array")
    return backup
