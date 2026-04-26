"""Trusted callback wrapper for metadata-transported callables.

Callbacks passed through message metadata dicts (platform_meta,
OutboundMessage.metadata) are wrapped in TrustedCallback at the producer site
and unwrapped at the consumer site. Raw callables found in metadata are
rejected — prevents code execution if an attacker controls the metadata source.
"""

from __future__ import annotations

import inspect
import logging
from collections.abc import Awaitable, Callable
from typing import Any

log = logging.getLogger(__name__)


class TrustedCallback[T]:
    """Wraps a callable to mark it as originating from trusted internal code."""

    __slots__ = ("fn",)

    def __init__(self, fn: Callable[..., T | Awaitable[T]]) -> None:
        self.fn = fn

    async def __call__(self, *args: Any, **kwargs: Any) -> T:
        result = self.fn(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result  # type: ignore[return-value]


def unwrap_callback(
    metadata: dict[str, Any],
    key: str,
    *,
    pop: bool = False,
) -> TrustedCallback[Any] | None:
    """Extract and validate a TrustedCallback from a metadata dict.

    Returns None (and logs a warning) if the value exists but is not a TrustedCallback.
    """
    value = metadata.pop(key, None) if pop else metadata.get(key)
    if value is None:
        return None
    if isinstance(value, TrustedCallback):
        return value
    log.warning(
        "Rejected untrusted callback in metadata key %r: %s",
        key,
        type(value).__qualname__,
    )
    return None
