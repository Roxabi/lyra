"""Unit tests for JsonFormatter and LoggingConfig (#270)."""

from __future__ import annotations

import json
import logging
import logging.handlers
from contextvars import copy_context

from lyra.bootstrap.config import LoggingConfig, _load_logging_config
from lyra.core.trace import JsonFormatter, TraceContext, TraceIdFilter

# ──────────────────────────────────────────────────────────────────────
# JsonFormatter
# ──────────────────────────────────────────────────────────────────────


class TestJsonFormatter:
    def _make_record(self, msg: str = "test message", **kwargs) -> logging.LogRecord:
        record = logging.LogRecord(
            name="lyra.core.hub",
            level=logging.INFO,
            pathname="hub.py",
            lineno=42,
            msg=msg,
            args=kwargs.get("args", ()),
            exc_info=None,
        )
        # Simulate TraceIdFilter having run
        record.trace_id = kwargs.get("trace_id", "")  # type: ignore[attr-defined]
        record.pool_id = kwargs.get("pool_id", "")  # type: ignore[attr-defined]
        return record

    def test_output_is_valid_json(self) -> None:
        fmt = JsonFormatter()
        record = self._make_record()
        line = fmt.format(record)
        obj = json.loads(line)
        assert isinstance(obj, dict)

    def test_allowlist_fields_present(self) -> None:
        fmt = JsonFormatter()
        record = self._make_record(trace_id="abc-123", pool_id="tg:main:chat:1")
        obj = json.loads(fmt.format(record))
        assert obj["level"] == "INFO"
        assert obj["logger"] == "lyra.core.hub"
        assert obj["message"] == "test message"
        assert obj["trace_id"] == "abc-123"
        assert obj["pool_id"] == "tg:main:chat:1"
        assert "timestamp" in obj

    def test_empty_trace_id_omitted(self) -> None:
        """Fields with empty string value are omitted from JSON."""
        fmt = JsonFormatter()
        record = self._make_record()
        obj = json.loads(fmt.format(record))
        assert "trace_id" not in obj
        assert "pool_id" not in obj

    def test_no_internal_logrecord_fields(self) -> None:
        """Internal fields like pathname, threadName must NOT appear."""
        fmt = JsonFormatter()
        record = self._make_record(trace_id="t1")
        obj = json.loads(fmt.format(record))
        forbidden = ("pathname", "threadName", "processName", "lineno")
        for key in forbidden:
            assert key not in obj

    def test_message_interpolation(self) -> None:
        """Format args should be interpolated into the message."""
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello %s count=%d",
            args=("world", 42),
            exc_info=None,
        )
        record.trace_id = ""  # type: ignore[attr-defined]
        record.pool_id = ""  # type: ignore[attr-defined]
        fmt = JsonFormatter()
        obj = json.loads(fmt.format(record))
        assert obj["message"] == "hello world count=42"

    def test_exception_in_separate_field(self) -> None:
        """Exception text should be in a separate 'exception' field."""
        try:
            raise ValueError("boom")
        except ValueError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="something failed",
            args=(),
            exc_info=exc_info,
        )
        record.trace_id = ""  # type: ignore[attr-defined]
        record.pool_id = ""  # type: ignore[attr-defined]
        fmt = JsonFormatter()
        obj = json.loads(fmt.format(record))
        assert obj["message"] == "something failed"
        assert "ValueError: boom" in obj["exception"]

    def test_one_line_per_record(self) -> None:
        """Each formatted record must be a single line (JSONL)."""
        fmt = JsonFormatter()
        record = self._make_record()
        line = fmt.format(record)
        assert "\n" not in line

    def test_one_line_per_record_with_exception(self) -> None:
        """JSONL invariant holds even with multi-line exception tracebacks."""
        try:
            raise RuntimeError("multi\nline\nerror")
        except RuntimeError:
            import sys

            exc_info = sys.exc_info()

        record = logging.LogRecord(
            name="test",
            level=logging.ERROR,
            pathname="",
            lineno=0,
            msg="fail",
            args=(),
            exc_info=exc_info,
        )
        record.trace_id = ""  # type: ignore[attr-defined]
        record.pool_id = ""  # type: ignore[attr-defined]
        fmt = JsonFormatter()
        line = fmt.format(record)
        assert "\n" not in line
        obj = json.loads(line)
        assert "exception" in obj

    def test_non_ascii_message(self) -> None:
        """Non-ASCII messages are preserved (ensure_ascii=False)."""
        fmt = JsonFormatter()
        record = self._make_record(msg="Привет мир 🌍")
        obj = json.loads(fmt.format(record))
        assert obj["message"] == "Привет мир 🌍"

    def test_integration_with_filter(self) -> None:
        """TraceIdFilter + JsonFormatter work together end-to-end."""
        f = TraceIdFilter()
        fmt = JsonFormatter()
        record = logging.LogRecord(
            name="lyra.test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="integrated test",
            args=(),
            exc_info=None,
        )

        def _inner():
            TraceContext.set_trace_id("int-trace-1")
            TraceContext.set_pool_id("tg:main:chat:99")
            f.filter(record)
            return fmt.format(record)

        ctx = copy_context()
        line = ctx.run(_inner)
        obj = json.loads(line)
        assert obj["trace_id"] == "int-trace-1"
        assert obj["pool_id"] == "tg:main:chat:99"
        assert obj["message"] == "integrated test"


# ──────────────────────────────────────────────────────────────────────
# LoggingConfig
# ──────────────────────────────────────────────────────────────────────


class TestSetupLogging:
    """Tests for _setup_logging wiring (#270)."""

    def test_json_file_true_uses_json_formatter(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("lyra.__main__.Path.home", lambda: tmp_path)
        import lyra.__main__ as main_mod

        # Clear any existing root handlers
        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            main_mod._setup_logging(LoggingConfig(json_file=True))
            file_handler = next(
                h
                for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )
            assert isinstance(file_handler.formatter, JsonFormatter)
        finally:
            root.handlers[:] = original_handlers

    def test_json_file_false_uses_plain_formatter(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("lyra.__main__.Path.home", lambda: tmp_path)
        import lyra.__main__ as main_mod

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            main_mod._setup_logging(LoggingConfig(json_file=False))
            file_handler = next(
                h
                for h in root.handlers
                if isinstance(h, logging.handlers.RotatingFileHandler)
            )
            assert not isinstance(file_handler.formatter, JsonFormatter)
        finally:
            root.handlers[:] = original_handlers

    def test_trace_filter_attached_to_root(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("lyra.__main__.Path.home", lambda: tmp_path)
        import lyra.__main__ as main_mod

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        original_filters = root.filters[:]
        root.handlers.clear()
        root.filters.clear()
        try:
            main_mod._setup_logging(LoggingConfig())
            assert any(isinstance(f, TraceIdFilter) for f in root.filters)
        finally:
            root.handlers[:] = original_handlers
            root.filters[:] = original_filters

    def test_duplicate_call_does_not_add_handlers(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr("lyra.__main__.Path.home", lambda: tmp_path)
        import lyra.__main__ as main_mod

        root = logging.getLogger()
        original_handlers = root.handlers[:]
        root.handlers.clear()
        try:
            main_mod._setup_logging(LoggingConfig())
            count_after_first = len(root.handlers)
            main_mod._setup_logging(LoggingConfig())
            assert len(root.handlers) == count_after_first
        finally:
            root.handlers[:] = original_handlers


class TestLoggingConfig:
    def test_default_json_file_true(self) -> None:
        cfg = LoggingConfig()
        assert cfg.json_file is True

    def test_override_json_file_false(self) -> None:
        cfg = LoggingConfig(json_file=False)
        assert cfg.json_file is False

    def test_load_from_raw_config(self) -> None:
        raw = {"logging": {"json_file": False}}
        cfg = _load_logging_config(raw)
        assert cfg.json_file is False

    def test_load_from_empty_config(self) -> None:
        cfg = _load_logging_config({})
        assert cfg.json_file is True  # default
