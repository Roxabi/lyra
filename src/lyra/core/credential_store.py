"""Compatibility shim — CredentialStore moved to lyra.core.stores (S1)."""
from .stores.credential_store import *  # noqa: F401, F403
from .stores.credential_store import BotSecretRow, CredentialStore, LyraKeyring

__all__ = ["BotSecretRow", "CredentialStore", "LyraKeyring"]
