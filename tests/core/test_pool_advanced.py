"""Pool advanced tests — unknown agent drain, cancel/inbox race, identity,
append, snapshot, and turn timeout ceiling.

Spec trace: S1, SC-4, SC-5, SC-13, #317, #341
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from unittest.mock import MagicMock

import pytest

from lyra.core.message import InboundMessage, Response
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel
from tests.core.conftest import _drain, make_msg

# ---------------------------------------------------------------------------
# File-local helper
# ---------------------------------------------------------------------------


def _make_inbound(
    user_id: str = "u1", platform: str = "telegram", text: str = "hello"
) -> InboundMessage:
    """Build a minimal InboundMessage for S1 tests."""
    return InboundMessage(
        id="s1-msg",
        platform=platform,
        bot_id="main",
        scope_id="chat:1",
        user_id=user_id,
        user_name="Test",
        is_mention=False,
        text=text,
        text_raw=text,
        timestamp=datetime.now(timezone.utc),
        platform_meta={
            "chat_id": 1,
            "topic_id": None,
            "message_id": None,
            "is_group": False,
        },
        trust_level=TrustLevel.TRUSTED,
    )


# ---------------------------------------------------------------------------
# Unknown agent drain
# ---------------------------------------------------------------------------


class TestPoolUnknownAgentDrain:
    """When get_agent returns None, inbox is drained gracefully (no crash)."""

    @pytest.mark.asyncio
    async def test_process_loop_unknown_agent_drains_inbox(
        self, ctx_mock: MagicMock
    ) -> None:
        """When the agent is not found, queued messages are drained without dispatch."""
        ctx_mock.get_agent.return_value = None  # no agent registered

        pool = Pool(
            pool_id="test:main:chat:noagent",
            agent_name="test_agent",
            ctx=ctx_mock,
            turn_timeout=60.0,
            debounce_ms=0,
        )

        msg1 = make_msg("msg1")
        msg2 = make_msg("msg2")
        pool._inbox.put_nowait(msg1)
        pool._inbox.put_nowait(msg2)

        task = asyncio.create_task(pool._processor.process_loop())
        await asyncio.sleep(0.1)

        ctx_mock.dispatch_response.assert_not_called()
        assert pool._inbox.empty()
        assert task.done()


# ---------------------------------------------------------------------------
# Cancel/inbox race
# ---------------------------------------------------------------------------


class TestPoolCancelInboxRace:
    """cancel-in-flight: agent_task and inbox_waiter complete simultaneously."""

    @pytest.mark.asyncio
    async def test_process_with_cancel_inbox_race(self, ctx_mock: MagicMock) -> None:
        """Two messages submitted rapidly — both are processed (second via re-queue)."""
        results: list[str] = []

        class RecordingAgent:
            name = "test_agent"

            async def process(
                self,
                msg: InboundMessage,
                pool: Pool,
                *,
                on_intermediate=None,
            ) -> Response:
                results.append(msg.text)
                return Response(content="ok")

        agent = RecordingAgent()
        ctx_mock._agents["test_agent"] = agent
        pool = Pool(
            pool_id="test:main:chat:race",
            agent_name="test_agent",
            ctx=ctx_mock,
            turn_timeout=60.0,
            debounce_ms=0,
        )

        msg1 = make_msg("first")
        msg2 = make_msg("second")
        pool.submit(msg1)
        pool.submit(msg2)

        await _drain(pool, timeout=3.0)

        combined = "\n".join(results)
        assert "first" in combined
        assert "second" in combined


# ---------------------------------------------------------------------------
# S1 — Pool identity / session fields (issue #83)
# ---------------------------------------------------------------------------


class TestPoolIdentity:
    """Pool must carry per-session identity metadata (S1)."""

    def test_pool_has_session_id(self, pool: Pool) -> None:
        """Pool.session_id must be a UUID4 string (36 chars with dashes)."""
        assert isinstance(pool.session_id, str) and len(pool.session_id) == 36

    def test_pool_identity_defaults(self, pool: Pool) -> None:
        """user_id, medium and message_count default to empty/zero on creation."""
        assert pool.user_id == "" and pool.medium == "" and pool.message_count == 0

    def test_pool_session_start_is_utc(self, pool: Pool) -> None:
        """session_start must be timezone-aware (UTC)."""
        assert pool.session_start.tzinfo is not None

    def test_pool_system_prompt_default(self, pool: Pool) -> None:
        """_system_prompt defaults to empty string before any message."""
        assert pool._system_prompt == ""

    def test_pool_turn_logger_default(self, pool: Pool) -> None:
        """_turn_logger defaults to None — no logger wired by default."""
        assert pool._turn_logger is None


# ---------------------------------------------------------------------------
# S1 — Pool.append()
# ---------------------------------------------------------------------------


class TestPoolAppend:
    """Pool.append() must update session identity fields (S1)."""

    def test_append_promotes_user_id_once(self, pool: Pool) -> None:
        """First append sets user_id; subsequent appends with different user_id
        must NOT overwrite it (first-writer-wins semantics)."""
        msg1 = _make_inbound(user_id="u1", platform="telegram")
        pool.append(msg1)
        assert pool.user_id == "u1" and pool.message_count == 1

        msg2 = _make_inbound(user_id="u2", platform="discord")
        pool.append(msg2)
        assert pool.user_id == "u1"  # not overwritten

    def test_append_increments_message_count(self, pool: Pool) -> None:
        """message_count increments once per append call."""
        pool.append(_make_inbound())
        pool.append(_make_inbound())
        assert pool.message_count == 2

    def test_append_sets_medium_from_platform(self, pool: Pool) -> None:
        """First append captures medium (platform string) from the message."""
        pool.append(_make_inbound(platform="telegram"))
        assert pool.medium == "telegram"

    def test_append_calls_turn_logger_no_error(self, pool: Pool) -> None:
        """When _turn_logger is set, append() must not raise any error."""
        calls: list = []

        async def fake_logger(sid: str, m: object) -> None:
            calls.append((sid, m))

        pool._turn_logger = fake_logger
        pool.append(_make_inbound())
        # turn_logger may be called via create_task; at minimum no sync error
        assert pool.message_count == 1


# ---------------------------------------------------------------------------
# S1 — Pool.snapshot()
# ---------------------------------------------------------------------------


class TestPoolSnapshot:
    """Pool.snapshot() must return a SessionSnapshot (S1)."""

    def test_snapshot_returns_session_snapshot(self, pool: Pool) -> None:
        """snapshot() returns a SessionSnapshot with correct identity fields."""
        from lyra.core.memory import SessionSnapshot

        pool.append(_make_inbound(user_id="u1", platform="telegram"))
        snap = pool.snapshot("lyra")
        assert isinstance(snap, SessionSnapshot)
        assert snap.session_id == pool.session_id
        assert snap.user_id == "u1"
        assert snap.medium == "telegram"
        assert snap.message_count == 1
        assert snap.session_end >= snap.session_start


# ---------------------------------------------------------------------------
# Turn timeout ceiling clamp (#317)
# ---------------------------------------------------------------------------


class TestTurnTimeoutCeiling:
    """#317: Pool.__init__() clamps turn_timeout to ceiling."""

    def test_uses_ceiling_when_no_override(self, ctx_mock: MagicMock) -> None:
        """SC-4: No agent override → ceiling becomes default."""
        pool = Pool(
            pool_id="p1",
            agent_name="a",
            ctx=ctx_mock,
            turn_timeout_ceiling=600,
        )
        assert pool._turn_timeout == 600

    def test_agent_below_ceiling_kept(self, ctx_mock: MagicMock) -> None:
        """SC-4: Agent override below ceiling → kept as-is."""
        pool = Pool(
            pool_id="p1",
            agent_name="a",
            ctx=ctx_mock,
            turn_timeout=300,
            turn_timeout_ceiling=600,
        )
        assert pool._turn_timeout == 300

    def test_agent_above_ceiling_clamped(self, ctx_mock: MagicMock) -> None:
        """SC-5: Agent override above ceiling → clamped to ceiling."""
        pool = Pool(
            pool_id="p1",
            agent_name="a",
            ctx=ctx_mock,
            turn_timeout=900,
            turn_timeout_ceiling=600,
        )
        assert pool._turn_timeout == 600

    def test_no_ceiling_no_override(self, ctx_mock: MagicMock) -> None:
        """SC-13: No ceiling and no override → None (backward compat)."""
        pool = Pool(pool_id="p1", agent_name="a", ctx=ctx_mock)
        assert pool._turn_timeout is None

    def test_agent_override_no_ceiling(self, ctx_mock: MagicMock) -> None:
        """Agent sets turn_timeout without ceiling → kept as-is."""
        pool = Pool(
            pool_id="p1",
            agent_name="a",
            ctx=ctx_mock,
            turn_timeout=300,
        )
        assert pool._turn_timeout == 300
