"""Tests for scope-based routing, cross-scope rate limiting, and scope-isolated pools."""  # noqa: E501

from __future__ import annotations

from datetime import datetime, timezone

from lyra.core import Hub
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, Platform
from tests.core.conftest import make_inbound_message

# ---------------------------------------------------------------------------
# T2 — Scope-based RoutingKey fields
# ---------------------------------------------------------------------------


class TestScopeIdRouting:
    """RoutingKey uses scope_id (not user_id) as the 3rd field."""

    def test_routing_key_has_scope_id_field(self) -> None:
        from lyra.core.hub import RoutingKey

        key = RoutingKey(
            platform=Platform.TELEGRAM,
            bot_id="main",
            scope_id="chat:555",
        )
        assert key.scope_id == "chat:555"
        assert not hasattr(key, "user_id")

    def test_to_pool_id_uses_scope_id(self) -> None:
        from lyra.core.hub import RoutingKey

        key = RoutingKey(
            platform=Platform.TELEGRAM,
            bot_id="main",
            scope_id="chat:555",
        )
        pool_id = key.to_pool_id()
        assert pool_id == "telegram:main:chat:555"


# ---------------------------------------------------------------------------
# T3 — Cross-scope rate limiting is per-user
# ---------------------------------------------------------------------------


class TestCrossScopeRateLimit:
    """Rate limiting is per-user regardless of scope."""

    def test_rate_limit_spans_scopes(self) -> None:
        # Arrange — two different scopes for the same user_id
        hub = Hub(rate_limit=5, rate_window=60)

        def make_scoped_message(scope_id: str) -> InboundMessage:
            return InboundMessage(
                id="msg-1",
                platform="telegram",
                bot_id="main",
                scope_id=scope_id,
                user_id="alice",
                user_name="Alice",
                is_mention=False,
                text="hello",
                text_raw="hello",
                timestamp=datetime.now(timezone.utc),
                platform_meta={
                    "chat_id": int(scope_id.split(":")[-1]),
                    "topic_id": None,
                    "message_id": None,
                    "is_group": False,
                },
                trust_level=TrustLevel.TRUSTED,
            )

        # Act — send RATE_LIMIT (5) messages, 3 from scope A and 2 from scope B
        results = []
        for _ in range(3):
            results.append(hub._is_rate_limited(make_scoped_message("chat:100")))
        for _ in range(2):
            results.append(hub._is_rate_limited(make_scoped_message("chat:200")))
        # The 6th message (RATE_LIMIT+1) must be rate-limited regardless of scope
        last_result = hub._is_rate_limited(make_scoped_message("chat:100"))

        # Assert — first 5 messages pass, 6th is rate-limited
        assert all(r is False for r in results), (
            f"Expected all first {hub._rate_limit} messages to pass, got {results}"
        )
        assert last_result is True, (
            "Expected 6th message to be rate-limited (rate limit spans scopes)"
        )
        # Key type must be a plain tuple (platform, bot_id, user_id),
        # NOT a RoutingKey — prevents scope-switching bypass.
        assert ("telegram", "main", "alice") in hub._rate_timestamps


# ---------------------------------------------------------------------------
# T4 — Scope-isolated pools
# ---------------------------------------------------------------------------


class TestScopeIsolatedPools:
    """Different scopes for the same user_id produce different Pool objects."""

    def test_scope_isolated_pools(self) -> None:
        # Arrange — wildcard binding; same user, two different chat_ids (scopes)
        hub = Hub()
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        msg_a = InboundMessage(
            id="msg-a",
            platform="telegram",
            bot_id="main",
            scope_id="chat:100",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            platform_meta={
                "chat_id": 100,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
            trust_level=TrustLevel.TRUSTED,
        )
        msg_b = InboundMessage(
            id="msg-b",
            platform="telegram",
            bot_id="main",
            scope_id="chat:200",
            user_id="alice",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            timestamp=datetime.now(timezone.utc),
            platform_meta={
                "chat_id": 200,
                "topic_id": None,
                "message_id": None,
                "is_group": False,
            },
            trust_level=TrustLevel.TRUSTED,
        )

        # Act — resolve binding and create pools for each scoped message
        binding_a = hub.resolve_binding(msg_a)
        binding_b = hub.resolve_binding(msg_b)
        assert binding_a is not None
        assert binding_b is not None

        pool_a = hub.get_or_create_pool(binding_a.pool_id, binding_a.agent_name)
        pool_b = hub.get_or_create_pool(binding_b.pool_id, binding_b.agent_name)

        # Assert — different scopes produce different pools with exact pool_ids
        assert pool_a is not pool_b, (
            "Expected different Pool objects for different scopes"
        )
        assert pool_a.pool_id == "telegram:main:chat:100"
        assert pool_b.pool_id == "telegram:main:chat:200"


# ---------------------------------------------------------------------------
# TestRateTimestampsCleanup — F6
# ---------------------------------------------------------------------------


class TestRateTimestampsCleanup:
    """_is_rate_limited() deletes the deque entry when all timestamps expire."""

    def test_deque_deleted_when_all_timestamps_expire(self) -> None:
        """When all timestamps in the sliding window fall outside the window, the
        deque is evicted from _rate_timestamps before a fresh entry is created.
        After the second call with advanced time, the deque contains exactly one
        timestamp — the new one — confirming the stale entry was removed."""
        import unittest.mock

        hub = Hub(rate_window=1)
        msg = make_inbound_message(platform="telegram", user_id="alice")

        # First call — inserts a timestamp for alice's user key
        hub._is_rate_limited(msg)
        key = (msg.platform, msg.bot_id, msg.user_id)
        assert key in hub._rate_timestamps
        assert len(hub._rate_timestamps[key]) == 1

        # Advance monotonic clock past the 1-second window
        now_plus_2 = hub._rate_timestamps[key][0] + 2
        with unittest.mock.patch(
            "lyra.core.hub.hub_rate_limit.time.monotonic", return_value=now_plus_2
        ):
            result = hub._is_rate_limited(msg)

        # Not rate-limited (only 1 message in the fresh window)
        assert result is False
        # The old timestamp was evicted; the deque now holds only the new call's
        # timestamp. This proves the cleanup path ran (deque was deleted and
        # re-created, not merely appended to).
        assert len(hub._rate_timestamps[key]) == 1
        assert hub._rate_timestamps[key][0] == now_plus_2
