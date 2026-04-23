"""Unit tests for middleware pipeline (#431).

Each middleware is tested independently with a mock ``next()`` callback —
no Hub required for most tests.
"""

from __future__ import annotations

import dataclasses
from typing import cast
from unittest.mock import AsyncMock, MagicMock, patch

from lyra.core.hub.middleware import (
    MiddlewarePipeline,
    PipelineContext,
    build_default_pipeline,
)
from lyra.core.hub.middleware.middleware_stages import (
    CommandMiddleware,
    CreatePoolMiddleware,
    RateLimitMiddleware,
    ResolveBindingMiddleware,
    ValidatePlatformMiddleware,
)
from lyra.core.hub.middleware.middleware_submit import SubmitToPoolMiddleware
from lyra.core.hub.middleware.path_validation import resolve_context
from lyra.core.hub.pipeline.message_pipeline import Action, PipelineResult, ResumeStatus
from lyra.core.messaging.message import Platform, Response
from lyra.infrastructure.stores.turn_store import TurnStore
from tests.core.conftest import _make_hub, make_inbound_message

_DROP = PipelineResult(action=Action.DROP)
_PASS = PipelineResult(action=Action.SUBMIT_TO_POOL)


def _make_next(result: PipelineResult = _PASS) -> AsyncMock:
    """Build a mock ``next`` callback returning *result*."""
    return AsyncMock(return_value=result)


def _make_ctx(**overrides) -> PipelineContext:
    """Build a PipelineContext with a real hub and optional overrides."""
    hub = _make_hub()
    ctx = PipelineContext(hub=hub)
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


# ──────────────────────────────────────────────────────────────────────
# ValidatePlatformMiddleware
# ──────────────────────────────────────────────────────────────────────


class TestValidatePlatform:
    async def test_valid_platform_calls_next(self) -> None:
        mw = ValidatePlatformMiddleware()
        ctx = _make_ctx()
        next_fn = _make_next()
        msg = make_inbound_message(platform="telegram")

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once()
        assert result == _PASS

    async def test_unknown_platform_drops(self) -> None:
        mw = ValidatePlatformMiddleware()
        ctx = _make_ctx()
        next_fn = _make_next()
        msg = make_inbound_message(platform="unknown_plat")

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.action == Action.DROP

    async def test_traces_platform_invalid(self) -> None:
        events: list[dict] = []

        def hook(stage, event, **kw):
            events.append({"stage": stage, "event": event, **kw})

        mw = ValidatePlatformMiddleware()
        ctx = _make_ctx(trace_hook=hook)
        msg = make_inbound_message(platform="bad_plat")

        await mw(msg, ctx, _make_next())

        assert any(e["event"] == "platform_invalid" for e in events)


# ──────────────────────────────────────────────────────────────────────
# RateLimitMiddleware
# ──────────────────────────────────────────────────────────────────────


class TestRateLimit:
    async def test_not_limited_calls_next(self) -> None:
        from lyra.core.hub.hub_protocol import RoutingKey

        mw = RateLimitMiddleware()
        ctx = _make_ctx()
        next_fn = _make_next()
        msg = make_inbound_message()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once()
        assert result == _PASS
        assert ctx.key == RoutingKey(Platform.TELEGRAM, "main", "chat:42")

    async def test_rate_limited_drops(self) -> None:
        mw = RateLimitMiddleware()
        hub = _make_hub(rate_limit=1, rate_window=60)
        ctx = PipelineContext(hub=hub)
        msg = make_inbound_message()

        # First call passes
        await mw(msg, ctx, _make_next())
        # Second call drops
        ctx2 = PipelineContext(hub=hub)
        result = await mw(msg, ctx2, _make_next())

        assert result.action == Action.DROP


# ──────────────────────────────────────────────────────────────────────
# ResolveBindingMiddleware
# ──────────────────────────────────────────────────────────────────────


class TestResolveBinding:
    async def test_binding_found_calls_next(self) -> None:
        from lyra.core.hub.hub_protocol import RoutingKey

        mw = ResolveBindingMiddleware()
        ctx = _make_ctx()
        ctx.key = RoutingKey(Platform.TELEGRAM, "main", "chat:42")
        next_fn = _make_next()
        msg = make_inbound_message()

        await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once()
        assert ctx.binding is not None
        assert ctx.agent is not None

    async def test_no_binding_drops(self) -> None:
        from lyra.core.hub import Hub
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = Hub()
        # No binding registered
        mw = ResolveBindingMiddleware()
        ctx = PipelineContext(hub=hub)
        ctx.key = RoutingKey(Platform.TELEGRAM, "main", "chat:42")
        msg = make_inbound_message()

        result = await mw(msg, ctx, _make_next())

        assert result.action == Action.DROP

    async def test_no_agent_drops(self) -> None:
        from lyra.core.hub import Hub
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = Hub()
        from tests.core.conftest import _MockAdapter

        # justified: test double for ResolveBindingMiddleware —
        # does not need full ChannelAdapter protocol
        hub.register_adapter(Platform.TELEGRAM, "main", _MockAdapter())
        hub.register_binding(Platform.TELEGRAM, "main", "*", "ghost", "telegram:main:*")
        mw = ResolveBindingMiddleware()
        ctx = PipelineContext(hub=hub)
        ctx.key = RoutingKey(Platform.TELEGRAM, "main", "chat:42")
        msg = make_inbound_message()

        result = await mw(msg, ctx, _make_next())

        assert result.action == Action.DROP


# ──────────────────────────────────────────────────────────────────────
# CreatePoolMiddleware
# ──────────────────────────────────────────────────────────────────────


class TestCreatePool:
    async def test_creates_pool_and_router(self) -> None:
        from lyra.core.hub.hub_protocol import Binding

        hub = _make_hub()
        agent = hub.agent_registry["lyra"]
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        mw = CreatePoolMiddleware()
        ctx = PipelineContext(hub=hub, binding=binding, agent=agent)
        next_fn = _make_next()
        msg = make_inbound_message()

        await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once()
        assert ctx.pool is not None

    async def test_on_resume_fn_wired_when_turn_store_present(self) -> None:
        from lyra.core.hub.hub_protocol import Binding

        hub = _make_hub()
        hub._turn_store = MagicMock()
        hub._turn_store.increment_resume_count = AsyncMock()
        agent = hub.agent_registry["lyra"]
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        mw = CreatePoolMiddleware()
        ctx = PipelineContext(hub=hub, binding=binding, agent=agent)
        msg = make_inbound_message()

        await mw(msg, ctx, _make_next())

        assert ctx.pool is not None
        assert ctx.pool._on_resume_fn is hub._turn_store.increment_resume_count  # type: ignore[attr-defined]

    async def test_on_resume_fn_not_set_when_turn_store_absent(self) -> None:
        from lyra.core.hub.hub_protocol import Binding

        hub = _make_hub()
        hub._turn_store = None
        agent = hub.agent_registry["lyra"]
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        mw = CreatePoolMiddleware()
        ctx = PipelineContext(hub=hub, binding=binding, agent=agent)
        msg = make_inbound_message()

        await mw(msg, ctx, _make_next())

        assert ctx.pool is not None
        assert ctx.pool._on_resume_fn is None  # type: ignore[attr-defined]

    async def test_on_resume_fn_not_overwritten_when_already_set(self) -> None:
        from lyra.core.hub.hub_protocol import Binding

        hub = _make_hub()
        hub._turn_store = MagicMock()
        hub._turn_store.increment_resume_count = AsyncMock()

        # Pre-create the pool and assign a sentinel
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        sentinel = MagicMock()
        pool._on_resume_fn = sentinel

        agent = hub.agent_registry["lyra"]
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        mw = CreatePoolMiddleware()
        ctx = PipelineContext(hub=hub, binding=binding, agent=agent)
        msg = make_inbound_message()

        await mw(msg, ctx, _make_next())

        assert ctx.pool is not None
        assert ctx.pool._on_resume_fn is sentinel  # type: ignore[attr-defined]


# ──────────────────────────────────────────────────────────────────────
# CommandMiddleware
# ──────────────────────────────────────────────────────────────────────


class TestCommand:
    async def test_non_command_calls_next(self) -> None:
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = _make_hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        mw = CommandMiddleware()
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
            router=None,
        )
        next_fn = _make_next()
        msg = make_inbound_message()

        await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once()

    async def test_command_dispatched(self) -> None:
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = _make_hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        router = MagicMock()
        router.is_command.return_value = True
        router.dispatch = AsyncMock(return_value=Response(content="ok"))

        mw = CommandMiddleware()
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
            router=router,
        )
        next_fn = _make_next()
        msg = make_inbound_message()

        result = await mw(msg, ctx, next_fn)

        assert result.action == Action.COMMAND_HANDLED
        assert result.response is not None
        assert result.response.content == "ok"
        next_fn.assert_not_awaited()

    async def test_command_fallthrough_calls_next(self) -> None:
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = _make_hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        router = MagicMock()
        router.is_command.return_value = True
        router.dispatch = AsyncMock(return_value=None)  # not found

        mw = CommandMiddleware()
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
            router=router,
        )
        next_fn = _make_next()
        msg = make_inbound_message()

        await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once()


# ──────────────────────────────────────────────────────────────────────
# SubmitToPoolMiddleware
# ──────────────────────────────────────────────────────────────────────


class TestSubmitToPool:
    async def test_submits_to_pool(self) -> None:
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = _make_hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
        )
        msg = make_inbound_message()
        mw = SubmitToPoolMiddleware()

        result = await mw(msg, ctx, _make_next())

        assert result.action == Action.SUBMIT_TO_POOL
        assert result.pool is pool

    async def test_no_adapter_drops(self) -> None:
        from lyra.core.hub import Hub
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = Hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
        )
        msg = make_inbound_message()
        mw = SubmitToPoolMiddleware()

        result = await mw(msg, ctx, _make_next())

        assert result.action == Action.DROP


# ──────────────────────────────────────────────────────────────────────
# MiddlewarePipeline (end-to-end chain)
# ──────────────────────────────────────────────────────────────────────


class TestMiddlewarePipelineChain:
    async def test_chain_order_preserved(self) -> None:
        """Middlewares execute in registration order."""
        order: list[str] = []

        class MW:
            def __init__(self, name: str):
                self._name = name

            async def __call__(self, msg, ctx, next):
                order.append(self._name)
                return await next(msg, ctx)

        class Terminal:
            async def __call__(self, msg, ctx, next):
                order.append("terminal")
                return _PASS

        hub = _make_hub()
        pipeline = MiddlewarePipeline([MW("a"), MW("b"), Terminal()], hub)
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        assert order == ["a", "b", "terminal"]
        assert result == _PASS

    async def test_short_circuit_stops_chain(self) -> None:
        """A middleware returning DROP stops the chain."""

        class Blocker:
            async def __call__(self, msg, ctx, next):
                return _DROP

        class Unreachable:
            async def __call__(self, msg, ctx, next):
                raise AssertionError("should not be reached")

        hub = _make_hub()
        pipeline = MiddlewarePipeline([Blocker(), Unreachable()], hub)
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        assert result.action == Action.DROP

    async def test_trace_hook_receives_message_received(self) -> None:
        events: list[dict] = []

        def hook(stage, event, **kw):
            events.append({"stage": stage, "event": event, **kw})

        hub = _make_hub()
        pipeline = build_default_pipeline(hub, trace_hook=hook)
        msg = make_inbound_message()

        await pipeline.process(msg)

        assert any(e["event"] == "message_received" for e in events)


class TestBuildDefaultPipeline:
    async def test_happy_path(self) -> None:
        """Full default pipeline routes a valid message to SUBMIT_TO_POOL."""
        hub = _make_hub()
        pipeline = build_default_pipeline(hub)
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        assert result.action == Action.SUBMIT_TO_POOL
        assert result.pool is not None

    async def test_unknown_platform_drops(self) -> None:
        hub = _make_hub()
        pipeline = build_default_pipeline(hub)
        msg = make_inbound_message(platform="unknown")

        result = await pipeline.process(msg)

        assert result.action == Action.DROP


# ──────────────────────────────────────────────────────────────────────
# Fix #3: CommandMiddleware error path
# ──────────────────────────────────────────────────────────────────────


class TestCommandErrorPath:
    async def test_dispatch_raises_returns_error_response(self) -> None:
        """Command dispatch exception → COMMAND_HANDLED with generic error."""
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = _make_hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        router = MagicMock()
        router.is_command.return_value = True
        router.dispatch = AsyncMock(side_effect=RuntimeError("boom"))

        mw = CommandMiddleware()
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
            router=router,
        )
        msg = make_inbound_message()

        result = await mw(msg, ctx, _make_next())

        assert result.action == Action.COMMAND_HANDLED
        assert result.response is not None
        assert "boom" not in (result.response.content or "")

    async def test_dispatch_raises_emits_command_error_trace(self) -> None:
        """Command dispatch exception fires command_error trace event."""
        from lyra.core.hub.hub_protocol import RoutingKey

        events: list[dict] = []

        def hook(stage, event, **kw):
            events.append({"stage": stage, "event": event, **kw})

        hub = _make_hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        router = MagicMock()
        router.is_command.return_value = True
        router.dispatch = AsyncMock(side_effect=RuntimeError("boom"))

        mw = CommandMiddleware()
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
            router=router,
            trace_hook=hook,
        )
        msg = make_inbound_message()

        await mw(msg, ctx, _make_next())

        assert any(e["event"] == "command_error" for e in events)


# ──────────────────────────────────────────────────────────────────────
# Fix #4: SubmitToPoolMiddleware circuit-breaker drop
# ──────────────────────────────────────────────────────────────────────


class TestCircuitBreakerDrop:
    async def test_circuit_breaker_open_drops(self) -> None:
        """Open circuit breaker in SubmitToPoolMiddleware → DROP."""
        from lyra.core.circuit_breaker import CircuitBreaker, CircuitRegistry
        from lyra.core.hub.hub_protocol import RoutingKey

        registry = CircuitRegistry()
        cb = CircuitBreaker(name="anthropic", failure_threshold=1, recovery_timeout=60)
        registry.register(cb)
        cb.record_failure()

        hub = _make_hub(circuit_registry=registry)
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
        )
        msg = make_inbound_message()
        mw = SubmitToPoolMiddleware()

        result = await mw(msg, ctx, _make_next())

        assert result.action == Action.DROP


# ──────────────────────────────────────────────────────────────────────
# Fix #5: _resolve_context resume paths (via SubmitToPoolMiddleware)
# ──────────────────────────────────────────────────────────────────────


class TestResolveContextMiddleware:
    async def test_no_thread_session_returns_skipped(self) -> None:
        """No thread_session_id and no TurnStore → SKIPPED."""
        hub = _make_hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        ctx = PipelineContext(hub=hub)
        msg = make_inbound_message()

        status = await resolve_context(msg, pool, pool.pool_id, ctx)

        assert status == ResumeStatus.SKIPPED

    async def test_thread_session_accepted_returns_resumed(self) -> None:
        """thread_session_id + accepted → RESUMED."""
        hub = _make_hub()
        pool_id = "telegram:main:chat:42"
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        # Wire fake TurnStore so scope validation passes (#525).
        class _FakeTurnStore:
            async def get_session_pool_id(self, session_id: str) -> str | None:
                return pool_id

            async def increment_resume_count(self, sid: str) -> None:
                pass

        hub._turn_store = cast(TurnStore, _FakeTurnStore())

        ctx = PipelineContext(hub=hub)
        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-1"}
        msg = dataclasses.replace(_base, platform_meta=_meta)

        status = await resolve_context(msg, pool, pool.pool_id, ctx)

        assert status == ResumeStatus.RESUMED

    async def test_thread_session_rejected_returns_fresh(self) -> None:
        """thread_session_id rejected → FRESH."""
        hub = _make_hub()
        pool_id = "telegram:main:chat:42"
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _fake_resume(sid: str) -> bool:
            return False

        pool._session_resume_fn = _fake_resume  # type: ignore[attr-defined]

        # Wire fake TurnStore so scope validation passes (#525).
        class _FakeTurnStore:
            async def get_session_pool_id(self, session_id: str) -> str | None:
                return pool_id

            async def get_last_session(self, pid: str) -> str | None:
                return None

            async def increment_resume_count(self, sid: str) -> None:
                pass

        hub._turn_store = cast(TurnStore, _FakeTurnStore())

        ctx = PipelineContext(hub=hub)
        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)

        status = await resolve_context(msg, pool, pool.pool_id, ctx)

        assert status == ResumeStatus.FRESH


# ──────────────────────────────────────────────────────────────────────
# Fix #6: PipelineContext.trace exception swallowing
# ──────────────────────────────────────────────────────────────────────


class TestTraceExceptionSwallowing:
    async def test_raising_trace_hook_does_not_propagate(self) -> None:
        """A raising trace_hook must not propagate exceptions."""

        def bad_hook(stage, event, **kw):
            raise RuntimeError("trace bug")

        ctx = _make_ctx(trace_hook=bad_hook)

        # Must not raise
        ctx.trace("stage", "event", key="value")

    async def test_raising_trace_in_pipeline_does_not_abort(self) -> None:
        """A raising trace_hook must not abort pipeline processing."""

        def bad_hook(stage, event, **kw):
            raise RuntimeError("trace bug")

        hub = _make_hub()
        pipeline = build_default_pipeline(hub, trace_hook=bad_hook)
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        assert result.action == Action.SUBMIT_TO_POOL


# ──────────────────────────────────────────────────────────────────────
# Fix #7: empty/exhausted pipeline → DROP
# ──────────────────────────────────────────────────────────────────────


class TestEmptyPipeline:
    async def test_empty_pipeline_drops(self) -> None:
        """A pipeline with no middlewares returns DROP."""
        hub = _make_hub()
        pipeline = MiddlewarePipeline([], hub)
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        assert result.action == Action.DROP

    async def test_next_past_last_middleware_drops(self) -> None:
        """Calling next past the last middleware returns DROP."""

        class PassThrough:
            async def __call__(self, msg, ctx, next):
                return await next(msg, ctx)

        hub = _make_hub()
        pipeline = MiddlewarePipeline([PassThrough()], hub)
        msg = make_inbound_message()

        result = await pipeline.process(msg)

        assert result.action == Action.DROP


# ──────────────────────────────────────────────────────────────────────
# Fix #8: _notify_session_fallthrough on FRESH
# ──────────────────────────────────────────────────────────────────────


class TestNotifySessionFallthroughMiddleware:
    async def test_notify_called_when_fresh(self) -> None:
        """FRESH status triggers try_notify_user."""
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = _make_hub()
        pool_id = "telegram:main:chat:42"
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _rejected_resume(sid: str) -> bool:
            return False

        pool._session_resume_fn = _rejected_resume  # type: ignore[attr-defined]

        # Wire fake TurnStore so scope validation passes (#525).
        class _FakeTurnStore:
            async def get_session_pool_id(self, session_id: str) -> str | None:
                return pool_id

            async def get_last_session(self, pid: str) -> str | None:
                return None

            async def increment_resume_count(self, sid: str) -> None:
                pass

        hub._turn_store = cast(TurnStore, _FakeTurnStore())

        _base = make_inbound_message(scope_id="chat:42")
        _meta = {**_base.platform_meta, "thread_session_id": "tss-dead"}
        msg = dataclasses.replace(_base, platform_meta=_meta)
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
        )

        notify_calls: list[tuple] = []

        async def _fake_notify(platform, _a, _o, text, **_kw):
            notify_calls.append((platform, text))

        _patch = "lyra.core.hub.outbound.outbound_errors.try_notify_user"
        mw = SubmitToPoolMiddleware()
        with patch(_patch, side_effect=_fake_notify):
            result = await mw(msg, ctx, _make_next())

        assert result.action == Action.SUBMIT_TO_POOL
        assert len(notify_calls) == 1
        assert "starting fresh" in notify_calls[0][1]
