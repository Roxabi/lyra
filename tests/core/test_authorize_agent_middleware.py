"""Unit tests for AuthorizeAgentMiddleware (ADR-090 §5, issue #1982)."""

from __future__ import annotations

import dataclasses
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, MagicMock

from factory.core.auth.agent_grants import AgentAuthorizer, AuthDecision
from factory.core.hub.middleware.middleware import PipelineContext
from factory.core.hub.middleware.middleware_authz import (
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
