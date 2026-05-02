"""Shared logging setup for all Lyra entry-points."""

from __future__ import annotations

import logging

from lyra.core.trace import TelegramTokenFilter, TraceIdFilter

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"


def setup_logging(level: str = "INFO") -> None:
    """Configure stdout-only logging with token redaction.

    Idempotent: returns immediately if the root logger already has handlers.
    Attaches TelegramTokenFilter to both the handler and the root logger so
    that httpx URL logs (which embed the bot token) are redacted before
    reaching any sink.
    """
    root = logging.getLogger()
    if root.handlers:
        return
    trace_filter = TraceIdFilter()
    telegram_filter = TelegramTokenFilter()
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter(_FMT))
    handler.addFilter(trace_filter)
    handler.addFilter(telegram_filter)
    level_int = getattr(logging, level.upper(), logging.INFO)
    root.setLevel(level_int)
    root.addFilter(trace_filter)
    root.addFilter(telegram_filter)
    root.addHandler(handler)
