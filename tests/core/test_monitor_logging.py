"""Tests for monitoring entry-point logging setup via shared helper (#1020)."""

from __future__ import annotations

import logging

from lyra.core.logging_setup import setup_logging
from lyra.core.trace import TelegramTokenFilter, TraceIdFilter


class TestMonitorLogging:
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

    def test_root_level_is_info(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            assert root.level == logging.INFO
        finally:
            self._restore(root, h, f, lv)

    def test_stream_handler_present(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            assert len(root.handlers) == 1
            assert isinstance(root.handlers[0], logging.StreamHandler)
        finally:
            self._restore(root, h, f, lv)

    def test_telegram_token_filter_attached(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            handler_filters = root.handlers[0].filters
            assert any(isinstance(f, TelegramTokenFilter) for f in handler_filters)
            assert any(isinstance(f, TraceIdFilter) for f in handler_filters)
        finally:
            self._restore(root, h, f, lv)

    def test_idempotent_on_double_call(self) -> None:
        root = logging.getLogger()
        h, f, lv = self._reset(root)
        try:
            setup_logging()
            count = len(root.handlers)
            setup_logging()
            assert len(root.handlers) == count
        finally:
            self._restore(root, h, f, lv)
