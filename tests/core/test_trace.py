"""Unit tests for trace context, filter, and middleware (#270)."""

from __future__ import annotations

import logging
from contextvars import copy_context
from unittest.mock import AsyncMock

from lyra.core.hub.message_pipeline import Action, PipelineResult
from lyra.core.hub.middleware import PipelineContext
from lyra.core.hub.middleware_stages import CreatePoolMiddleware, TraceMiddleware
from lyra.core.trace import TraceContext, TraceIdFilter
from tests.core.conftest import _make_hub, make_inbound_message

# ──────────────────────────────────────────────────────────────────────
# TraceContext
# ──────────────────────────────────────────────────────────────────────


class TestTraceContext:
    def test_generate_returns_uuid_string(self) -> None:
        tid = TraceContext.generate()
        assert isinstance(tid, str)
        assert len(tid) == 36  # UUID4 format: 8-4-4-4-12

    def test_generate_unique(self) -> None:
        ids = {TraceContext.generate() for _ in range(100)}
        assert len(ids) == 100

    def test_get_trace_id_returns_none_when_unset(self) -> None:
        ctx = copy_context()
        assert ctx.run(TraceContext.get_trace_id) is None

    def test_set_and_get_trace_id(self) -> None:
        def _inner():
            TraceContext.set_trace_id("test-123")
            return TraceContext.get_trace_id()

        ctx = copy_context()
        assert ctx.run(_inner) == "test-123"

    def test_get_pool_id_returns_none_when_unset(self) -> None:
        ctx = copy_context()
        assert ctx.run(TraceContext.get_pool_id) is None

    def test_set_and_get_pool_id(self) -> None:
        def _inner():
            TraceContext.set_pool_id("telegram:main:chat:42")
            return TraceContext.get_pool_id()

        ctx = copy_context()
        assert ctx.run(_inner) == "telegram:main:chat:42"


# ──────────────────────────────────────────────────────────────────────
# TraceIdFilter
# ──────────────────────────────────────────────────────────────────────


class TestTraceIdFilter:
    def test_filter_always_returns_true(self) -> None:
        f = TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )
        assert f.filter(record) is True

    def test_filter_sets_trace_id_from_contextvar(self) -> None:
        f = TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )

        def _inner():
            TraceContext.set_trace_id("abc-def")
            f.filter(record)
            return record.trace_id  # type: ignore[attr-defined]

        ctx = copy_context()
        assert ctx.run(_inner) == "abc-def"

    def test_filter_sets_pool_id_from_contextvar(self) -> None:
        f = TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )

        def _inner():
            TraceContext.set_pool_id("tg:main:chat:1")
            f.filter(record)
            return record.pool_id  # type: ignore[attr-defined]

        ctx = copy_context()
        assert ctx.run(_inner) == "tg:main:chat:1"

    def test_filter_sets_empty_when_no_context(self) -> None:
        """When no contextvar is set, attributes are empty string."""
        f = TraceIdFilter()
        record = logging.LogRecord(
            name="test",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg="hello",
            args=(),
            exc_info=None,
        )

        ctx = copy_context()
        ctx.run(f.filter, record)
        assert record.trace_id == ""  # type: ignore[attr-defined]
        assert record.pool_id == ""  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────
# TraceMiddleware
# ──────────────────────────────────────────────────────────────────────


_PASS = PipelineResult(action=Action.SUBMIT_TO_POOL)


def _make_next(result: PipelineResult = _PASS) -> AsyncMock:
    return AsyncMock(return_value=result)


def _make_ctx(**overrides) -> PipelineContext:
    hub = _make_hub()
    ctx = PipelineContext(hub=hub)
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


class TestTraceMiddleware:
    async def test_sets_trace_id_before_next(self) -> None:
        """trace_id must be available when next() runs."""
        captured_ids: list[str | None] = []

        async def _capturing_next(msg, ctx):
            captured_ids.append(TraceContext.get_trace_id())
            return _PASS

        mw = TraceMiddleware()
        msg = make_inbound_message()
        ctx = _make_ctx()

        await mw(msg, ctx, _capturing_next)

        assert len(captured_ids) == 1
        assert captured_ids[0] is not None
        assert len(captured_ids[0]) == 36  # UUID4

    async def test_each_call_gets_unique_trace_id(self) -> None:
        ids: list[str | None] = []

        async def _capturing_next(msg, ctx):
            ids.append(TraceContext.get_trace_id())
            return _PASS

        mw = TraceMiddleware()
        msg = make_inbound_message()

        await mw(msg, _make_ctx(), _capturing_next)
        await mw(msg, _make_ctx(), _capturing_next)

        assert len(ids) == 2
        assert ids[0] != ids[1]

    async def test_calls_next_and_returns_result(self) -> None:
        mw = TraceMiddleware()
        next_fn = _make_next()
        msg = make_inbound_message()

        result = await mw(msg, _make_ctx(), next_fn)

        next_fn.assert_awaited_once()
        assert result == _PASS


# ──────────────────────────────────────────────────────────────────────
# CreatePoolMiddleware — pool_id ContextVar
# ──────────────────────────────────────────────────────────────────────


class TestPoolIdContextVar:
    async def test_create_pool_sets_pool_id_contextvar(self) -> None:
        """CreatePoolMiddleware must set pool_id in TraceContext."""
        from unittest.mock import MagicMock

        from lyra.core.hub.hub import Binding, RoutingKey
        from lyra.core.message import Platform

        captured_pool_ids: list[str | None] = []

        async def _capturing_next(msg, ctx):
            captured_pool_ids.append(TraceContext.get_pool_id())
            return _PASS

        hub = _make_hub()
        binding = Binding(
            pool_id="telegram:main:chat:42",
            agent_name="test_agent",
        )
        key = RoutingKey(Platform.TELEGRAM, "main", "chat:42")

        # Set up mock agent in registry
        mock_agent = MagicMock()
        mock_agent.name = "test_agent"
        hub.agent_registry["test_agent"] = mock_agent

        ctx = PipelineContext(hub=hub, key=key, binding=binding, agent=mock_agent)

        mw = CreatePoolMiddleware()
        msg = make_inbound_message()

        await mw(msg, ctx, _capturing_next)

        assert len(captured_pool_ids) == 1
        assert captured_pool_ids[0] == "telegram:main:chat:42"


# ──────────────────────────────────────────────────────────────────────
# TelegramTokenFilter
# ──────────────────────────────────────────────────────────────────────


class TestTelegramTokenFilter:
    """Redact Telegram bot tokens from log messages (security fix, 2026-04-20)."""

    def _make_record(self, msg: str, *args: object) -> logging.LogRecord:
        return logging.LogRecord(
            name="httpx",
            level=logging.INFO,
            pathname="",
            lineno=0,
            msg=msg,
            args=args if args else None,
            exc_info=None,
        )

    def test_redacts_token_in_telegram_url(self) -> None:
        from lyra.core.trace import TelegramTokenFilter

        filt = TelegramTokenFilter()
        url = (
            "https://api.telegram.org/"
            "bot8500388193:AAGg_wDfJ7896yPdf-L10CEVHbiuShA38Sw/sendMessage"
        )
        record = self._make_record(f'HTTP Request: POST {url} "HTTP/1.1 200 OK"')
        assert filt.filter(record) is True
        assert "AAGg_wDfJ7896yPdf-L10CEVHbiuShA38Sw" not in record.getMessage()
        assert "bot8500388193:<REDACTED>" in record.getMessage()

    def test_preserves_bot_id_for_debuggability(self) -> None:
        from lyra.core.trace import TelegramTokenFilter

        filt = TelegramTokenFilter()
        record = self._make_record(
            "POST https://api.telegram.org/bot123:AAA_bbb-CCC111/getMe"
        )
        filt.filter(record)
        # Bot id (123) kept so operators can still identify which bot was called.
        assert "bot123:<REDACTED>" in record.getMessage()

    def test_redacts_in_args_interpolation(self) -> None:
        """% args format — filter must run getMessage() to expose the token."""
        from lyra.core.trace import TelegramTokenFilter

        filt = TelegramTokenFilter()
        record = self._make_record(
            "calling %s", "https://api.telegram.org/bot999:secret_token_xyz-123/sendMessage"
        )
        filt.filter(record)
        assert "secret_token_xyz-123" not in record.getMessage()

    def test_passes_through_unrelated_messages(self) -> None:
        from lyra.core.trace import TelegramTokenFilter

        filt = TelegramTokenFilter()
        record = self._make_record("normal log line, no secrets here")
        filt.filter(record)
        assert record.getMessage() == "normal log line, no secrets here"

    def test_never_suppresses_records(self) -> None:
        from lyra.core.trace import TelegramTokenFilter

        filt = TelegramTokenFilter()
        record = self._make_record("anything")
        assert filt.filter(record) is True
