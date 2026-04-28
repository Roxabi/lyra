"""Tests for lyra.monitoring._setup_monitor_logging (#999)."""

from __future__ import annotations

import logging

from lyra.core.trace import TelegramTokenFilter, TraceIdFilter


class TestSetupMonitorLogging:
    def test_root_level_is_info(self) -> None:
        from lyra.monitoring.__main__ import _setup_monitor_logging

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        original_level = root.level
        root.handlers.clear()
        root.filters.clear()
        try:
            _setup_monitor_logging()
            assert root.level == logging.INFO
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters
            root.setLevel(original_level)

    def test_stream_handler_present(self) -> None:
        from lyra.monitoring.__main__ import _setup_monitor_logging

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        original_level = root.level
        root.handlers.clear()
        root.filters.clear()
        try:
            _setup_monitor_logging()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0], logging.StreamHandler)
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters
            root.setLevel(original_level)

    def test_telegram_token_filter_attached(self) -> None:
        from lyra.monitoring.__main__ import _setup_monitor_logging

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        original_level = root.level
        root.handlers.clear()
        root.filters.clear()
        try:
            _setup_monitor_logging()
            handler_filters = root.handlers[0].filters
            assert any(isinstance(f, TelegramTokenFilter) for f in handler_filters)
            assert any(isinstance(f, TraceIdFilter) for f in handler_filters)
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters
            root.setLevel(original_level)

    def test_idempotent_on_double_call(self) -> None:
        from lyra.monitoring.__main__ import _setup_monitor_logging

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        original_level = root.level
        root.handlers.clear()
        root.filters.clear()
        try:
            _setup_monitor_logging()
            count_after_first = len(root.handlers)
            _setup_monitor_logging()
            assert len(root.handlers) == count_after_first
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters
            root.setLevel(original_level)
