"""Unit tests for middleware pipeline (#431).

Each middleware is tested independently with a mock ``next()`` callback —
no Hub required for most tests.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lyra.core.hub.message_pipeline import Action, PipelineResult
from lyra.core.hub.middleware import (
    CommandMiddleware,
    CreatePoolMiddleware,
    MiddlewarePipeline,
    PipelineContext,
    RateLimitMiddleware,
    ResolveBindingMiddleware,
    SubmitToPoolMiddleware,
    ValidatePlatformMiddleware,
    build_default_pipeline,
)
from lyra.core.message import Platform, Response
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
        mw = RateLimitMiddleware()
        ctx = _make_ctx()
        next_fn = _make_next()
        msg = make_inbound_message()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once()
        assert result == _PASS
        assert ctx.key is not None

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

        hub.register_adapter(Platform.TELEGRAM, "main", _MockAdapter())  # type: ignore[arg-type]
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
        mw = SubmitToPoolMiddleware()
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
        )
        msg = make_inbound_message()

        result = await mw(msg, ctx, _make_next())

        assert result.action == Action.SUBMIT_TO_POOL
        assert result.pool is pool

    async def test_no_adapter_drops(self) -> None:
        from lyra.core.hub import Hub
        from lyra.core.hub.hub_protocol import RoutingKey

        hub = Hub()
        pool = hub.get_or_create_pool("telegram:main:chat:42", "lyra")
        mw = SubmitToPoolMiddleware()
        ctx = PipelineContext(
            hub=hub,
            pool=pool,
            key=RoutingKey(Platform.TELEGRAM, "main", "chat:42"),
        )
        msg = make_inbound_message()

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
