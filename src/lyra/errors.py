"""Provider-agnostic exceptions for LLM drivers."""

from __future__ import annotations


class ProviderError(Exception):
    """Provider-agnostic wrapper for LLM SDK errors.

    Raised by LLM drivers so that core code can react to provider failures
    without importing any SDK-specific exception.
    """


class MissingCredentialsError(Exception):
    def __init__(self, platform: str, bot_id: str) -> None:
        super().__init__(
            f"No credentials found for {platform}/{bot_id}. "
            f"Run: lyra bot add --platform {platform} --bot-id {bot_id} --token <token>"
        )
        self.platform = platform
        self.bot_id = bot_id


class KeyringError(Exception):
    def __init__(self, path: str, reason: str) -> None:
        super().__init__(
            f"Keyring error at {path}: {reason}. "
            f"Re-create with: rm {path} && lyra bot add ..."
        )
        self.path = path
        self.reason = reason
