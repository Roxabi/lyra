"""Cross-layer exception classes.

These exceptions are raised and caught across multiple layers.
They live in the innermost layer (core) to avoid downward imports.
"""

from typing import Literal


class StreamChunkTimeout(TimeoutError):
    """Raised when the outbound chunk queue was idle past the timeout threshold."""


class ScrapeFailed(Exception):
    """Raised when the scraper fails or is not available."""

    def __init__(
        self, reason: Literal["not_available", "timeout", "subprocess_error"]
    ) -> None:
        self.reason = reason
        super().__init__(reason)


class VaultWriteFailed(Exception):
    """Raised when the vault write fails or is not available."""

    def __init__(self, reason: str = "") -> None:
        self.reason = reason
        super().__init__(reason)
