"""Per-request trace context, log filter, and JSON formatter (#270).

Provides:
- ``TraceContext`` — two ``contextvars.ContextVar`` instances (trace_id, pool_id)
  that propagate transparently through asyncio await chains.
- ``TraceIdFilter`` — a ``logging.Filter`` that reads both context vars and
  injects them into every ``LogRecord``.  Defensive: never raises.
- ``JsonFormatter`` — emits one JSON object per line from an explicit field
  allowlist. No ``LogRecord.__dict__`` dump.
"""

from __future__ import annotations

import json
import logging
import uuid
from contextvars import ContextVar, Token

_trace_id: ContextVar[str] = ContextVar("trace_id")
_pool_id: ContextVar[str] = ContextVar("pool_id")


class TraceContext:
    """Static helpers around the two tracing ContextVars."""

    @staticmethod
    def generate() -> str:
        """Return a new random UUID4 string."""
        return str(uuid.uuid4())

    @staticmethod
    def get_trace_id() -> str | None:
        return _trace_id.get(None)

    @staticmethod
    def set_trace_id(value: str) -> Token[str]:
        return _trace_id.set(value)

    @staticmethod
    def reset_trace_id(token: Token[str]) -> None:
        _trace_id.reset(token)

    @staticmethod
    def get_pool_id() -> str | None:
        return _pool_id.get(None)

    @staticmethod
    def set_pool_id(value: str) -> Token[str]:
        return _pool_id.set(value)

    @staticmethod
    def reset_pool_id(token: Token[str]) -> None:
        _pool_id.reset(token)


class TraceIdFilter(logging.Filter):
    """Inject ``trace_id`` and ``pool_id`` from context vars into log records.

    Attached to handlers at startup.  Always returns ``True`` — the record
    is never suppressed.  If a context var is unset the corresponding
    attribute is set to ``""`` (empty string) so formatters can rely on
    ``record.trace_id`` / ``record.pool_id`` existing.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            record.trace_id = _trace_id.get("")  # type: ignore[attr-defined]
        except Exception:
            record.trace_id = ""  # type: ignore[attr-defined]
        try:
            record.pool_id = _pool_id.get("")  # type: ignore[attr-defined]
        except Exception:
            record.pool_id = ""  # type: ignore[attr-defined]
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line with fields: timestamp, level, logger,
    message, trace_id, pool_id, exception, stack_info.

    Fields whose value is empty string are omitted from the output to keep
    JSON clean (e.g. ``trace_id`` is absent for startup log lines).
    """

    def format(self, record: logging.LogRecord) -> str:
        # Ensure the message is fully interpolated.
        record.message = record.getMessage()
        if record.exc_info and not record.exc_text:
            record.exc_text = self.formatException(record.exc_info)

        obj: dict[str, str] = {
            "timestamp": self.formatTime(record, self.datefmt),
            "level": record.levelname,
            "logger": record.name,
            "message": record.message,
        }

        # Exception and stack_info as separate fields (not in message).
        if record.exc_text:
            obj["exception"] = record.exc_text

        if record.stack_info:
            obj["stack_info"] = self.formatStack(record.stack_info)

        # Add context var fields only when non-empty.
        trace_id = getattr(record, "trace_id", "")
        if trace_id:
            obj["trace_id"] = trace_id

        pool_id = getattr(record, "pool_id", "")
        if pool_id:
            obj["pool_id"] = pool_id

        return json.dumps(obj, ensure_ascii=False)
