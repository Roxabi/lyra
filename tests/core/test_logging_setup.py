"""Unit tests for lyra.core.logging_setup.setup_logging (#1020)."""

from __future__ import annotations

import io
import logging

from lyra.bootstrap.factory.config import LoggingConfig, _load_logging_config
from lyra.core.logging_setup import setup_logging
from lyra.core.trace import TelegramTokenFilter, TraceIdFilter

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


class TestSetupLogging:
    def _reset(self, root: logging.Logger) -> tuple[list, list, int]:
        handlers = root.handlers[:]
        filters = root.filters[:]
        level = root.level
        root.handlers.clear()
        root.filters.clear()
        return handlers, filters, level

    def _restore(self, root: logging.Logger, h: list, f: list, lv: int) -> None:
        root.handlers[:] = h
        root.filters[:] = f
        root.setLevel(lv)

    def test_trace_filter_attached_to_root(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            assert any(isinstance(x, TraceIdFilter) for x in root.filters)
        finally:
            self._restore(root, h, f, lv)

    def test_telegram_token_filter_attached_to_root(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            assert any(isinstance(x, TelegramTokenFilter) for x in root.filters)
        finally:
            self._restore(root, h, f, lv)

    def test_telegram_token_filter_attached_to_handler(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            handler_filters = root.handlers[0].filters
            assert any(isinstance(x, TelegramTokenFilter) for x in handler_filters)
            assert any(isinstance(x, TraceIdFilter) for x in handler_filters)
        finally:
            self._restore(root, h, f, lv)

    def test_duplicate_call_does_not_add_handlers(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            count = len(root.handlers)
            setup_logging()
            assert len(root.handlers) == count
        finally:
            self._restore(root, h, f, lv)

    def test_level_applied_to_root(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging(level="debug")
            assert root.level == logging.DEBUG
        finally:
            self._restore(root, h, f, lv)

    def test_console_handler_only(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0], logging.StreamHandler)
        finally:
            self._restore(root, h, f, lv)

    def test_token_redacted_in_log_output(self) -> None:
        """With setup_logging active, bot token is redacted in captured output."""
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        stream = io.StringIO()
        try:
            setup_logging()
            assert isinstance(root.handlers[0], logging.StreamHandler)
            root.handlers[0].stream = stream
            logger = logging.getLogger("test_redact")
            logger.info("POST https://api.telegram.org/bot123456:ABCxyz/sendMessage")
            output = stream.getvalue()
            assert "bot123456:ABCxyz" not in output
            assert "<REDACTED>" in output
        finally:
            self._restore(root, h, f, lv)

    def test_token_appears_without_filter(self) -> None:
        """Negative: without TelegramTokenFilter the raw token is visible."""
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        logger = logging.getLogger("test_no_filter_negative")
        logger.propagate = False
        logger.addHandler(handler)
        logger.setLevel(logging.INFO)
        try:
            logger.info("POST https://api.telegram.org/bot123456:ABCxyz/sendMessage")
            output = stream.getvalue()
            assert "bot123456:ABCxyz" in output
        finally:
            logger.removeHandler(handler)
            logger.propagate = True
