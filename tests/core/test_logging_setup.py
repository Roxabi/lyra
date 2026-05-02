"""Unit tests for lyra.core.logging_setup.setup_logging (#1020)."""

from __future__ import annotations

import io
import logging
from collections.abc import Generator
from unittest.mock import patch

import pytest

import lyra.core.logging_setup as _ls_mod
from lyra.bootstrap.factory.config import LoggingConfig, _load_logging_config
from lyra.core.logging_setup import setup_logging
from lyra.core.trace import TelegramTokenFilter, TraceIdFilter

# ──────────────────────────────────────────────────────────────────────
# Fixture: reset module sentinel + root logger state before each test
# ──────────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_logging_state() -> Generator[None, None, None]:
    """Reset setup_logging sentinel and root logger state around each test.

    Clears root handlers/filters so setup_logging() starts from a clean
    slate. Pytest's log-capture plugin re-adds its own LogCaptureHandler
    subclasses around the test call body — tests that need to inspect
    handler specifics use `type(h) is logging.StreamHandler` to select
    only the handler installed by setup_logging().
    """
    root = logging.getLogger()
    orig_handlers = root.handlers[:]
    orig_filters = root.filters[:]
    orig_level = root.level
    orig_done = _ls_mod._setup_done
    root.handlers.clear()
    root.filters.clear()
    _ls_mod._setup_done = False
    yield
    root.handlers[:] = orig_handlers
    root.filters[:] = orig_filters
    root.setLevel(orig_level)
    _ls_mod._setup_done = orig_done


# ──────────────────────────────────────────────────────────────────────
# LoggingConfig
# ──────────────────────────────────────────────────────────────────────


class TestLoggingConfig:
    def test_default_level_is_info(self) -> None:
        cfg = LoggingConfig()
        assert cfg.level == "info"

    def test_override_level(self) -> None:
        cfg = LoggingConfig(level="debug")
        assert cfg.level == "debug"

    def test_load_from_raw_config(self) -> None:
        raw = {"logging": {"level": "debug"}}
        cfg = _load_logging_config(raw)
        assert cfg.level == "debug"

    def test_load_from_empty_config(self) -> None:
        cfg = _load_logging_config({})
        assert cfg.level == "info"  # default


# ──────────────────────────────────────────────────────────────────────
# setup_logging
# ──────────────────────────────────────────────────────────────────────


def _our_handler(root: logging.Logger) -> logging.StreamHandler:  # type: ignore[type-arg]
    """Return the StreamHandler installed by setup_logging (exact type match).

    Pytest's log-capture plugin injects LogCaptureHandler subclasses around
    the test body; `type(h) is logging.StreamHandler` skips those.
    """
    return next(h for h in root.handlers if type(h) is logging.StreamHandler)


class TestSetupLogging:
    def test_trace_filter_attached_to_handler_only(self) -> None:
        root = logging.getLogger()
        setup_logging()
        h = _our_handler(root)
        assert any(isinstance(x, TraceIdFilter) for x in h.filters)
        assert not any(isinstance(x, TraceIdFilter) for x in root.filters)

    def test_telegram_token_filter_attached_to_root(self) -> None:
        root = logging.getLogger()
        setup_logging()
        assert any(isinstance(x, TelegramTokenFilter) for x in root.filters)

    def test_telegram_token_filter_attached_to_handler(self) -> None:
        root = logging.getLogger()
        setup_logging()
        h = _our_handler(root)
        assert any(isinstance(x, TelegramTokenFilter) for x in h.filters)
        assert any(isinstance(x, TraceIdFilter) for x in h.filters)

    def test_duplicate_call_does_not_add_handlers_or_filters(self) -> None:
        root = logging.getLogger()
        setup_logging()
        handler_count = len(root.handlers)
        filter_count = len(root.filters)
        setup_logging()
        assert len(root.handlers) == handler_count
        assert len(root.filters) == filter_count

    def test_level_applied_to_root(self) -> None:
        root = logging.getLogger()
        setup_logging(level="debug")
        assert root.level == logging.DEBUG

    def test_level_updated_on_second_call(self) -> None:
        root = logging.getLogger()
        setup_logging(level="info")
        setup_logging(level="debug")
        assert root.level == logging.DEBUG

    def test_console_handler_only(self) -> None:
        """setup_logging installs exactly one native StreamHandler."""
        root = logging.getLogger()
        setup_logging()
        our = [h for h in root.handlers if type(h) is logging.StreamHandler]
        assert len(our) == 1

    def test_token_redacted_in_log_output(self) -> None:
        """With setup_logging active, bot token is redacted; numeric id preserved."""
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            setup_logging()
            logger = logging.getLogger("test_redact")
            logger.info("POST https://api.telegram.org/bot123456:ABCxyz/sendMessage")
            output = mock_stdout.getvalue()
        assert "bot123456:ABCxyz" not in output
        assert "<REDACTED>" in output
        assert "bot123456" in output  # numeric id preserved for correlation

    def test_token_appears_without_filter(self) -> None:
        """Negative: removing TelegramTokenFilter makes the raw token visible."""
        root = logging.getLogger()
        with patch("sys.stdout", new_callable=io.StringIO) as mock_stdout:
            setup_logging()
            # Remove TelegramTokenFilter to prove it is the causal mechanism
            root.filters = [
                f for f in root.filters if not isinstance(f, TelegramTokenFilter)
            ]
            for h in root.handlers:
                h.filters = [
                    f for f in h.filters if not isinstance(f, TelegramTokenFilter)
                ]
            logger = logging.getLogger("test_no_filter_negative")
            logger.info("POST https://api.telegram.org/bot123456:ABCxyz/sendMessage")
            output = mock_stdout.getvalue()
        assert "bot123456:ABCxyz" in output
