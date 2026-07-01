"""Unit tests for AuthorizeAgentMiddleware (ADR-090 §5, issue #1982)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from factory.core.auth.agent_grants import AgentAuthorizer, AuthDecision
from factory.core.hub.middleware.middleware import PipelineContext
from factory.core.hub.middleware.middleware_authz import (
    _DEFAULT_PUBLIC_SURFACE,
    _FALLBACK_REFUSAL,
    AuthorizeAgentMiddleware,
)
from factory.core.hub.pipeline.pipeline_events import MessageDropped
from factory.core.hub.pipeline.pipeline_types import Action, PipelineResult
from tests.core.conftest import _make_hub, make_inbound_message

if TYPE_CHECKING:
    from factory.infrastructure.stores.identity.agent_grant_store import AgentGrantStore


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_PASS = PipelineResult(action=Action.SUBMIT_TO_POOL)


def _make_next(result: PipelineResult = _PASS) -> AsyncMock:
    return AsyncMock(return_value=result)


def _make_ctx(**overrides) -> PipelineContext:
    hub = _make_hub()
    ctx = PipelineContext(hub=hub)
    for k, v in overrides.items():
        setattr(ctx, k, v)
    return ctx


def _make_authorizer(allowed: bool, reason: str = "") -> MagicMock:
    """Return a synchronous mock AgentAuthorizer.

    Returns ``MagicMock`` (not ``AgentAuthorizer``) so that pyright knows
    ``.authorize`` carries the full mock assertion API (assert_called_once_with,
    assert_not_called, …) rather than treating it as a plain ``MethodType``.
    """
    mock = MagicMock(spec=AgentAuthorizer)
    if allowed:
        mock.authorize.return_value = AuthDecision.allow(reason or "grant matched")
    else:
        mock.authorize.return_value = AuthDecision.deny(reason or "no matching grant")
    return mock


# ---------------------------------------------------------------------------
# TestDenySafePassthrough — ctx.binding/agent is None
# ---------------------------------------------------------------------------


class TestDenySafePassthrough:
    async def test_no_binding_passes_through(self) -> None:
        """When ctx.binding is None, skip authz and call next."""
        msg = make_inbound_message()
        agent_mock = MagicMock()
        # binding=None explicitly (default), agent set to prove it's the binding check
        ctx = _make_ctx(binding=None, agent=agent_mock)
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once_with(msg, ctx)
        authorizer.authorize.assert_not_called()
        assert result is _PASS

    async def test_no_agent_passes_through(self) -> None:
        """When ctx.agent is None, skip authz and call next."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message()
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        # binding set, agent=None
        ctx = _make_ctx(binding=binding, agent=None)
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once_with(msg, ctx)
        authorizer.authorize.assert_not_called()
        assert result is _PASS


# ---------------------------------------------------------------------------
# TestFailOpen — no authorizer wired
# ---------------------------------------------------------------------------


class TestFailOpen:
    async def test_no_authorizer_passes_through(self) -> None:
        """Fail-open: authorizer=None means skip authz and call next."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message()
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        ctx = _make_ctx(binding=binding, agent=agent_mock)
        mw = AuthorizeAgentMiddleware(authorizer=None)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once_with(msg, ctx)
        assert result is _PASS


# ---------------------------------------------------------------------------
# TestAuthorizedPath — decision.allowed == True
# ---------------------------------------------------------------------------


class TestAdminBypass:
    async def test_admin_bypasses_without_grant(self) -> None:
        """ADR-090 §1: [admin].user_ids bypass agent grant check via is_admin."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="tg:user:admin")
        msg = dataclasses.replace(msg, is_admin=True)
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once_with(msg, ctx)
        authorizer.authorize.assert_not_called()
        assert result is _PASS


class TestAuthorizedPath:
    async def test_authorized_passes_through(self) -> None:
        """When authorizer returns allowed=True, call next unchanged."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        ctx = _make_ctx(binding=binding, agent=agent_mock)
        authorizer = _make_authorizer(allowed=True)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once_with(msg, ctx)
        authorizer.authorize.assert_called_once_with(
            agent_name="lyra",
            user_id="alice",
            roles=(),
        )
        assert result is _PASS

    async def test_authorized_passes_roles_to_authorizer(self) -> None:
        """msg.roles tuple is forwarded to authorizer.authorize."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        msg = dataclasses.replace(msg, roles=("admin", "user"))
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        ctx = _make_ctx(binding=binding, agent=agent_mock)
        authorizer = _make_authorizer(allowed=True)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        await mw(msg, ctx, next_fn)

        authorizer.authorize.assert_called_once_with(
            agent_name="lyra",
            user_id="alice",
            roles=("admin", "user"),
        )


# ---------------------------------------------------------------------------
# TestUnauthorizedPath — decision.allowed == False
# ---------------------------------------------------------------------------


class TestUnauthorizedPath:
    async def test_unauthorized_returns_command_handled(self) -> None:
        """Deny path: action=COMMAND_HANDLED, next is never called."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        ctx = _make_ctx(binding=binding, agent=agent_mock)
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.action == Action.COMMAND_HANDLED
        assert result.response is not None

    async def test_unauthorized_emits_message_dropped(self) -> None:
        """Deny path emits MessageDropped(reason='agent_unauthorized')."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        event_bus = MagicMock()
        ctx = _make_ctx(binding=binding, agent=agent_mock, event_bus=event_bus)
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        event_bus.emit.assert_called_once()
        emitted = event_bus.emit.call_args[0][0]
        assert isinstance(emitted, MessageDropped)
        assert emitted.msg_id == msg.id
        assert emitted.stage == "AuthorizeAgentMiddleware"
        assert emitted.reason == "agent_unauthorized"

    async def test_unauthorized_uses_fallback_refusal(self) -> None:
        """When hub has no 'agent_unauthorized' message, use _FALLBACK_REFUSAL."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        ctx = _make_ctx(binding=binding, agent=agent_mock)
        # _make_hub() returns a hub with get_message returning None by default
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.response is not None
        assert result.response.content == _FALLBACK_REFUSAL

    async def test_unauthorized_uses_hub_message_when_configured(self) -> None:
        """When hub.get_message returns a custom text, use it over _FALLBACK_REFUSAL."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        hub = _make_hub()
        hub_mock = MagicMock(wraps=hub)
        hub_mock.get_message.return_value = "Custom unauthorized text"
        ctx = _make_ctx(binding=binding, agent=agent_mock)
        ctx.hub = hub_mock
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.response is not None
        assert result.response.content == "Custom unauthorized text"

    async def test_unauthorized_emits_trace_event(self) -> None:
        """Deny path calls ctx.trace with 'pool'/'agent_unauthorized'."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        agent_mock = MagicMock()
        trace_events: list[dict] = []

        def hook(stage: str, event: str, **kw: object) -> None:
            trace_events.append({"stage": stage, "event": event, **kw})

        ctx = _make_ctx(binding=binding, agent=agent_mock, trace_hook=hook)
        authorizer = _make_authorizer(allowed=False)
        mw = AuthorizeAgentMiddleware(authorizer=authorizer)
        next_fn = _make_next()

        await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert any(
            e["stage"] == "pool" and e["event"] == "agent_unauthorized"
            for e in trace_events
        ), f"Expected trace event pool/agent_unauthorized, got: {trace_events}"


# ---------------------------------------------------------------------------
# TestRealStoreIntegration — drive the actual AgentGrantStore port impl
# ---------------------------------------------------------------------------


class TestRealStoreIntegration:
    """End-to-end: the stage drives a real ``AgentGrantStore`` (not a mock).

    Proves the SQLite store satisfies the ``AgentAuthorizer`` protocol *as the
    stage consumes it*, and that kind-aware user/role matching (ADR-090 §1)
    flows through the stage's ``authorize(...)`` call.
    """

    async def test_user_grant_authorizes(
        self, agent_grant_store: AgentGrantStore
    ) -> None:
        from factory.core.auth.agent_grants import Principal, PrincipalKind
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="tg:user:alice")
        await agent_grant_store.grant(
            "lyra",
            Principal(kind=PrincipalKind.USER, id="tg:user:alice"),
            granted_by="test",
            source="test",
        )
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        mw = AuthorizeAgentMiddleware(authorizer=agent_grant_store)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once_with(msg, ctx)
        assert result is _PASS

    async def test_role_grant_authorizes(
        self, agent_grant_store: AgentGrantStore
    ) -> None:
        from factory.core.auth.agent_grants import Principal, PrincipalKind
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="tg:user:bob")
        msg = dataclasses.replace(msg, roles=("dc:role:admin",))
        await agent_grant_store.grant(
            "lyra",
            Principal(kind=PrincipalKind.ROLE, id="dc:role:admin"),
            granted_by="test",
            source="test",
        )
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        mw = AuthorizeAgentMiddleware(authorizer=agent_grant_store)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_awaited_once_with(msg, ctx)
        assert result is _PASS

    async def test_no_grant_refuses(self, agent_grant_store: AgentGrantStore) -> None:
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="tg:user:nobody")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        mw = AuthorizeAgentMiddleware(authorizer=agent_grant_store)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.action == Action.COMMAND_HANDLED
        assert result.response is not None
        assert result.response.content == _FALLBACK_REFUSAL

    async def test_grant_on_other_agent_refuses(
        self, agent_grant_store: AgentGrantStore
    ) -> None:
        """Grants are agent-scoped (ADR-090 §1): a USE grant on one agent does
        not leak authorization to another bound agent."""
        from factory.core.auth.agent_grants import Principal, PrincipalKind
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="tg:user:alice")
        # alice holds a grant on "other-agent" — but the bound agent is "lyra".
        await agent_grant_store.grant(
            "other-agent",
            Principal(kind=PrincipalKind.USER, id="tg:user:alice"),
            granted_by="test",
            source="test",
        )
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        mw = AuthorizeAgentMiddleware(authorizer=agent_grant_store)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.action == Action.COMMAND_HANDLED


# ---------------------------------------------------------------------------
# TestPipelineComposition — stage placement + authorizer forwarding (AC8/AC9)
# ---------------------------------------------------------------------------


class TestPipelineComposition:
    """Lock the stage's position in the default pipeline and the wiring kwarg.

    Guards against a reorder that would run authz before binding resolution
    (ADR-090 key invariant) or a dropped ``authorizer`` argument that would
    silently leave the stage fail-open in production.
    """

    async def test_runs_after_binding_before_prep(self) -> None:
        """ADR-090 §5 ordering invariant — asserted *relatively*.

        Index-agnostic so an unrelated stage added elsewhere in the pipeline
        cannot falsely break the contract; the real invariant is the ordering,
        not the absolute position.
        """
        from factory.core.hub.middleware import build_default_pipeline
        from factory.core.hub.middleware.middleware_pool import (
            MessagePrepMiddleware,
            ResolveBindingMiddleware,
        )

        stages = build_default_pipeline(_make_hub())._middlewares
        binding_idx = next(
            i for i, s in enumerate(stages) if isinstance(s, ResolveBindingMiddleware)
        )
        authz_idx = next(
            i for i, s in enumerate(stages) if isinstance(s, AuthorizeAgentMiddleware)
        )
        prep_idx = next(
            i for i, s in enumerate(stages) if isinstance(s, MessagePrepMiddleware)
        )

        assert binding_idx < authz_idx < prep_idx

    async def test_default_pipeline_shape_snapshot(self) -> None:
        """Deliberate snapshot of the default pipeline shape (spec AC8).

        Guards against an accidental insertion/removal of a stage. Update this
        intentionally — together with the spec — when the pipeline shape changes.
        """
        from factory.core.hub.middleware import build_default_pipeline

        stages = build_default_pipeline(_make_hub())._middlewares

        assert len(stages) == 10
        assert isinstance(stages[6], AuthorizeAgentMiddleware)

    async def test_authorizer_forwarded_to_stage(self) -> None:
        from factory.core.hub.middleware import build_default_pipeline

        sentinel = _make_authorizer(allowed=True)
        pipeline = build_default_pipeline(_make_hub(), authorizer=sentinel)
        stage = next(
            s for s in pipeline._middlewares if isinstance(s, AuthorizeAgentMiddleware)
        )

        assert stage._authorizer is sentinel


# ---------------------------------------------------------------------------
# TestPublicBotRefusal — ADR-090 §5 pointer interpolation (#1984)
# ---------------------------------------------------------------------------


def _formatting_hub() -> MagicMock:
    """A hub whose get_message interpolates the refusal template like the real one."""
    hub = MagicMock(wraps=_make_hub())
    hub.get_message.side_effect = lambda key, **kw: (
        f"visit {kw['public_bot']}" if key == "agent_unauthorized" else None
    )
    return hub


class TestPublicBotRefusal:
    async def test_refusal_points_to_bound_public_bot(self) -> None:
        """A bound public_bot handle is interpolated into the deny refusal."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(
            agent_name="lyra",
            pool_id="telegram:main:chat:42",
            public_bot="@bot_public",
        )
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        ctx.hub = _formatting_hub()
        mw = AuthorizeAgentMiddleware(authorizer=_make_authorizer(allowed=False))
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.response is not None
        assert result.response.content == "visit @bot_public"
        ctx.hub.get_message.assert_called_once_with(
            "agent_unauthorized", public_bot="@bot_public"
        )

    async def test_refusal_degrades_to_default_surface(self) -> None:
        """No public_bot on the binding → refusal degrades to the generic surface."""
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="alice")
        binding = Binding(agent_name="lyra", pool_id="telegram:main:chat:42")
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        ctx.hub = _formatting_hub()
        mw = AuthorizeAgentMiddleware(authorizer=_make_authorizer(allowed=False))
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        assert result.response is not None
        assert result.response.content == f"visit {_DEFAULT_PUBLIC_SURFACE}"
        ctx.hub.get_message.assert_called_once_with(
            "agent_unauthorized", public_bot=_DEFAULT_PUBLIC_SURFACE
        )

    async def test_public_bot_does_not_bypass_deny(
        self, agent_grant_store: AgentGrantStore
    ) -> None:
        """Security invariant: a public_bot handle is a pointer, not a grant.

        A sender with no grant is still refused even when the bound route
        declares a public_bot — deny-by-default (ADR-090 §1) is preserved.
        """
        from factory.core.hub.hub_protocol import Binding

        msg = make_inbound_message(user_id="tg:user:nobody")
        binding = Binding(
            agent_name="lyra",
            pool_id="telegram:main:chat:42",
            public_bot="@bot_public",
        )
        ctx = _make_ctx(binding=binding, agent=MagicMock())
        mw = AuthorizeAgentMiddleware(authorizer=agent_grant_store)
        next_fn = _make_next()

        result = await mw(msg, ctx, next_fn)

        next_fn.assert_not_awaited()
        assert result.action == Action.COMMAND_HANDLED
        assert result.response is not None
