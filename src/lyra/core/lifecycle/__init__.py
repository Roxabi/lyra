"""Lifecycle subpackage — circuit breaker, debouncer, session lifecycle."""

from __future__ import annotations

from .circuit_breaker import (
    CircuitBreaker,
    CircuitOpenError,
    CircuitRegistry,
    CircuitState,
    CircuitStatus,
)
from .debouncer import MessageDebouncer
from .session_lifecycle import COMPACT_THRESHOLD, MODEL_CONTEXT_TOKENS, SessionManager

__all__ = [
    "COMPACT_THRESHOLD",
    "CircuitBreaker",
    "CircuitOpenError",
    "CircuitRegistry",
    "CircuitState",
    "CircuitStatus",
    "MessageDebouncer",
    "MODEL_CONTEXT_TOKENS",
    "SessionManager",
]
