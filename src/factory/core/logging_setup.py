"""Shared logging setup for all Lyra entry-points."""

from __future__ import annotations

import logging
import sys

from factory.core.trace import TelegramTokenFilter, TraceIdFilter

_FMT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Module-level sentinel — guards handler/filter attachment, not level updates.
# Using a sentinel (rather than root.handlers check) ensures the filter contract
# holds even when a third-party library (e.g. pytest log-capture) pre-populates
# root handlers before setup_logging is called.
_setup_done: bool = False


def setup_logging(level: str = "INFO") -> None:
    """Configure stdout logging with token redaction.

    Always applies the requested log level to the root logger. Handler and
    filter attachment runs only once — subsequent calls update the level but
    skip re-attaching handlers/filters.

    Attaches TelegramTokenFilter to both the handler and the root logger so
    that httpx URL logs (which embed the bot token) are redacted before
    reaching any sink. TraceIdFilter is handler-only (injects ContextVar
    fields before emit; root-level attachment would be redundant).
    """
    global _setup_done
    root = logging.getLogger()
    level_int = getattr(logging, level.upper(), logging.INFO)
    # Always overrides: last caller wins. Intentional — entry-points call once at
    # startup; a second call (e.g. a test fixture) is expected to control the level.
    root.setLevel(level_int)
    if _setup_done:
        return
    trace_filter = TraceIdFilter()
    telegram_filter = TelegramTokenFilter()
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(logging.Formatter(_FMT))
    handler.addFilter(trace_filter)
    handler.addFilter(telegram_filter)
    # root-level filter: covers httpx loggers that propagate to root before
    # reaching any handler. handler-level filter: defence-in-depth for
    # future handlers added without the filter.
    root.addFilter(telegram_filter)
    root.addHandler(handler)
    _setup_done = True
