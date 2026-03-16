"""Provider-agnostic exceptions for LLM drivers."""

from __future__ import annotations


class ProviderError(Exception):
    """Provider-agnostic wrapper for LLM SDK errors.

    Raised by LLM drivers so that core code can react to provider failures
    without importing any SDK-specific exception.
    """

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        provider: str = "",
        retryable: bool = True,
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.provider = provider
        self.retryable = retryable


class ProviderAuthError(ProviderError):
    """Authentication or authorization failure (non-retryable)."""

    def __init__(
        self,
        message: str = "authentication failed",
        *,
        status_code: int | None = None,
        provider: str = "",
    ) -> None:
        super().__init__(
            message, status_code=status_code, provider=provider, retryable=False
        )


class ProviderRateLimitError(ProviderError):
    """Rate-limit / quota exceeded (retryable after back-off)."""

    def __init__(
        self,
        message: str = "rate limited",
        *,
        status_code: int | None = None,
        provider: str = "",
    ) -> None:
        super().__init__(
            message, status_code=status_code, provider=provider, retryable=True
        )


class ProviderApiError(ProviderError):
    """Generic API error from the provider (non-retryable by default)."""

    def __init__(
        self,
        message: str = "",
        *,
        status_code: int | None = None,
        provider: str = "",
        retryable: bool = False,
    ) -> None:
        super().__init__(
            message,
            status_code=status_code,
            provider=provider,
            retryable=retryable,
        )


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
