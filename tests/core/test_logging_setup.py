"""Unit tests for LoggingConfig and _setup_logging wiring (#999)."""

from __future__ import annotations

import logging

from lyra.bootstrap.factory.config import LoggingConfig, _load_logging_config
from lyra.core.trace import (  # noqa: F401 (used in isinstance checks)
    TelegramTokenFilter,
    TraceIdFilter,
)

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
# _setup_logging
# ──────────────────────────────────────────────────────────────────────


class TestSetupLogging:
    """Tests for _setup_logging wiring (#999)."""

    def test_trace_filter_attached_to_root(self) -> None:
        import lyra.__main__ as main_mod

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        root.handlers.clear()
        root.filters.clear()
        try:
            main_mod._setup_logging()
            assert any(isinstance(f, TraceIdFilter) for f in root.filters)
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters

    def test_duplicate_call_does_not_add_handlers(self) -> None:
        import lyra.__main__ as main_mod

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        root.handlers.clear()
        root.filters.clear()
        try:
            main_mod._setup_logging()
            count_after_first = len(root.handlers)
            main_mod._setup_logging()
            assert len(root.handlers) == count_after_first
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters

    def test_level_applied_to_root(self) -> None:
        import lyra.__main__ as main_mod

        root = logging.getLogger()
        original_level = root.level
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        root.handlers.clear()
        root.filters.clear()
        try:
            main_mod._setup_logging(level="debug")
            assert root.level == logging.DEBUG
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters
            root.setLevel(original_level)

    def test_console_handler_only(self) -> None:
        """After setup, only a StreamHandler is present (no file handler)."""
        import lyra.__main__ as main_mod

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        root.handlers.clear()
        root.filters.clear()
        try:
            main_mod._setup_logging()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0], logging.StreamHandler)
            handler_filters = root.handlers[0].filters
            assert any(isinstance(f, TraceIdFilter) for f in handler_filters)
            assert any(isinstance(f, TelegramTokenFilter) for f in handler_filters)
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters
