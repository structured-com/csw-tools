"""Small, testable adapter around the system keyring."""

from __future__ import annotations

from dataclasses import dataclass

import keyring
from keyring.errors import KeyringError


class KeyringStoreError(RuntimeError):
    """Raised when the active keyring backend cannot complete an operation."""


@dataclass(frozen=True, slots=True)
class KeyringStore:
    """Read and write passwords under one keyring service name."""

    service_name: str

    def get_password(self, username: str) -> str | None:
        """Return a password, or ``None`` when the username is not stored."""

        try:
            return keyring.get_password(self.service_name, username)
        except KeyringError as exc:
            raise KeyringStoreError(
                f"Could not read password for username '{username}' from keyring"
            ) from exc

    def set_password(self, username: str, password: str) -> None:
        """Store a password without exposing it in output or error messages."""

        try:
            keyring.set_password(self.service_name, username, password)
        except KeyringError as exc:
            raise KeyringStoreError(
                f"Could not store password for username '{username}' in keyring"
            ) from exc
