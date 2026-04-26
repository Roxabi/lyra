"""Per-request trace context, log filter, and JSON formatter (#270).

Provides:
- ``TraceContext`` — three ``contextvars.ContextVar`` instances (trace_id, pool_id,
  agent_name) that propagate transparently through asyncio await chains.
- ``TraceIdFilter`` — a ``logging.Filter`` that reads all context vars and
  injects them into every ``LogRecord``.  Defensive: never raises.
- ``TelegramTokenFilter`` — a ``logging.Filter`` that redacts Telegram bot
  tokens from log messages. ``httpx``'s default ``INFO`` log for every
  request includes the full URL, which for Telegram looks like
  ``POST https://api.telegram.org/bot<TOKEN>/sendMessage`` — the token is
  a credential and must not reach disk.
- ``JsonFormatter`` — emits one JSON object per line from an explicit field
  allowlist. No ``LogRecord.__dict__`` dump.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from contextvars import ContextVar, Token

_trace_id: ContextVar[str] = ContextVar("trace_id")
_pool_id: ContextVar[str] = ContextVar("pool_id")
_agent_name: ContextVar[str] = ContextVar("agent_name")


class TraceContext:
    """Static helpers around the three tracing ContextVars."""

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

    @staticmethod
    def get_agent_name() -> str | None:
        return _agent_name.get(None)

    @staticmethod
    def set_agent_name(value: str) -> Token[str]:
        return _agent_name.set(value)

    @staticmethod
    def reset_agent_name(token: Token[str]) -> None:
        _agent_name.reset(token)


class TraceIdFilter(logging.Filter):
    """Inject trace_id, pool_id, and agent_name from context vars into log records.

    Attached to handlers at startup.  Always returns ``True`` — the record
    is never suppressed.  If a context var is unset the corresponding
    attribute is set to ``""`` (empty string) so formatters can rely on
    ``record.trace_id`` / ``record.pool_id`` / ``record.agent_name`` existing.
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
        try:
            record.agent_name = _agent_name.get("")  # type: ignore[attr-defined]
        except Exception:
            record.agent_name = ""  # type: ignore[attr-defined]
        return True


class TelegramTokenFilter(logging.Filter):
    """Redact Telegram bot tokens from log messages.

    Telegram bot HTTP endpoints embed the token in the URL path
    (``/bot<token>/<method>``), and ``httpx`` logs the full URL at INFO
    level by default. The token grants full control over the bot — it must
    never appear in persisted logs, crash reports, or log-shipping
    pipelines.

    Matches ``bot<digits>:<alphanumeric-with-hyphens-underscores>`` and
    replaces the token segment with ``<REDACTED>``. Always returns
    ``True`` — the record is never suppressed. Defensive: never raises.
    """

    # Bot tokens: 1234567890:AAEhBP0av28...Z (numeric id, colon, ~35 char secret)
    _TOKEN_RE = re.compile(r"bot(\d+):[A-Za-z0-9_-]+")
    _REDACTED_SUB = r"bot\1:<REDACTED>"

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            # Format the message once (% args interpolation) then redact.
            msg = record.getMessage()
            redacted = self._TOKEN_RE.sub(self._REDACTED_SUB, msg)
            if redacted != msg:
                record.msg = redacted
                record.args = None
        except Exception:
            pass  # never block logging on a filter error
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per line with fields: timestamp, level, logger,
    message, trace_id, pool_id, agent_name, exception, stack_info.

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

        agent_name = getattr(record, "agent_name", "")
        if agent_name:
            obj["agent_name"] = agent_name

        return json.dumps(obj, ensure_ascii=False)
