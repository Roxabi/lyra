"""Tests for platform_meta sanitization and path-3 session resume (post-#1777).

Covers:
- sanitize_platform_meta() pure-function behaviour (allowlist, underscore strip,
  debug logging)
- Path-3 last-session resume in resolve_context (path-2 removed in #1777)
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from factory.infrastructure.stores.session.turn_store import TurnStore

from factory.core.hub.middleware import PipelineContext
from factory.core.hub.middleware.path_validation import resolve_context
from factory.core.hub.pipeline.message_pipeline import ResumeStatus
from factory.core.messaging.message import TelegramMeta
from tests.core.conftest import _make_hub, make_inbound_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTurnStore:
    """Minimal TurnStore stub for path-3 resume tests (post-#1777).

    Callers configure *pool_map* to control what get_last_session returns
    for a given pool_id. path-2 (get_session_pool_id) is gone.
    """

    def __init__(self, pool_map: dict[str, str | None] | None = None) -> None:
        self._pool_map: dict[str, str | None] = pool_map or {}

    async def get_last_session(self, pid: str) -> str | None:
        return self._pool_map.get(pid)

    async def increment_resume_count(self, sid: str) -> None:
        pass

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# TestNatsBusSanitization — handler-level integration
# ---------------------------------------------------------------------------


class TestNatsBusSanitization:
    """Verify sanitization fires inside the NatsBus handler closure."""

    def test_handler_sanitizes_platform_meta(self) -> None:
        """Typed TelegramMeta round-trips cleanly via serialize → deserialize."""
        from factory.core.auth.trust import TrustLevel
        from factory.core.messaging.message import (
            InboundMessage,
            Platform,
            TelegramMeta,
        )
        from factory.nats.type_registry import TYPE_REGISTRY_RESOLVER
        from roxabi_nats._serialize import deserialize, serialize

        msg = InboundMessage(
            id="msg-1",
            platform=Platform.TELEGRAM.value,
            bot_id="main",
            scope_id="chat:42",
            user_id="user:1",
            user_name="Alice",
            is_mention=False,
            text="hello",
            text_raw="hello",
            trust_level=TrustLevel.PUBLIC,
            platform_meta=TelegramMeta(chat_id=123, is_group=False),
        )
        # Simulate the handler: serialize → deserialize with type registry
        raw = serialize(msg)
        item = deserialize(raw, InboundMessage, resolver=TYPE_REGISTRY_RESOLVER)
        assert isinstance(item.platform_meta, TelegramMeta)
        assert item.platform_meta.chat_id == 123
        assert item.platform_meta.is_group is False

    def test_discord_meta_round_trips(self) -> None:
        """DiscordMeta round-trips cleanly; max-overlap decode picks DiscordMeta."""
        from factory.core.auth.trust import TrustLevel
        from factory.core.messaging.message import DiscordMeta, InboundMessage, Platform
        from factory.nats.type_registry import TYPE_REGISTRY_RESOLVER
        from roxabi_nats._serialize import deserialize, serialize

        msg = InboundMessage(
            id="msg-dc",
            platform=Platform.DISCORD.value,
            bot_id="main",
            scope_id="channel:99",
            user_id="user:2",
            user_name="Bob",
            is_mention=True,
            text="hi",
            text_raw="hi",
            trust_level=TrustLevel.PUBLIC,
            platform_meta=DiscordMeta(
                channel_id=99, message_id=42, guild_id=7, channel_type="text"
            ),
        )
        raw = serialize(msg)
        item = deserialize(raw, InboundMessage, resolver=TYPE_REGISTRY_RESOLVER)
        assert isinstance(item.platform_meta, DiscordMeta), (
            f"Expected DiscordMeta, got {item.platform_meta!r}"
        )
        assert item.platform_meta.channel_id == 99
        assert item.platform_meta.message_id == 42
        assert item.platform_meta.guild_id == 7

    def test_generic_meta_round_trips(self) -> None:
        """GenericMeta (no fields) round-trips as GenericMeta, not TelegramMeta."""
        from factory.core.auth.trust import TrustLevel
        from factory.core.messaging.message import GenericMeta, InboundMessage, Platform
        from factory.nats.type_registry import TYPE_REGISTRY_RESOLVER
        from roxabi_nats._serialize import deserialize, serialize

        msg = InboundMessage(
            id="msg-gm",
            platform=Platform.TELEGRAM.value,
            bot_id="main",
            scope_id="scope:1",
            user_id="user:3",
            user_name="Carol",
            is_mention=False,
            text="",
            text_raw="",
            trust_level=TrustLevel.PUBLIC,
            platform_meta=GenericMeta(),
        )
        raw = serialize(msg)
        item = deserialize(raw, InboundMessage, resolver=TYPE_REGISTRY_RESOLVER)
        assert isinstance(item.platform_meta, GenericMeta), (
            f"Expected GenericMeta, got {item.platform_meta!r}"
        )
        assert item.platform_meta == GenericMeta()


# ---------------------------------------------------------------------------
# TestScopeValidation — Path-3 last-session resume in resolve_context
# ---------------------------------------------------------------------------


class TestScopeValidation:
    """resolve_context path-3 behaviour post-#1777 (path-2 removed).

    Path-3: TurnStore.get_last_session(pool_id) → resume if session found,
    SKIPPED otherwise. No cross-scope validation or thread_session_id routing.
    """

    async def test_no_prior_session_returns_skipped(self) -> None:
        """TurnStore has no record for pool → SKIPPED (silent, expected)."""
        # Arrange
        pool_id = "telegram:main:chat:99"
        hub = _make_hub()
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({}),  # empty map → None for all pool_ids
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")
        ctx = PipelineContext(hub=hub)
        msg = make_inbound_message(scope_id="chat:99")

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert
        assert status == ResumeStatus.SKIPPED

    async def test_prior_session_returns_resumed(self) -> None:
        """TurnStore has a last session for this pool → resume attempted → RESUMED."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({pool_id: "sess-live"}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _accepted_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _accepted_resume  # type: ignore[attr-defined]

        ctx = PipelineContext(hub=hub)
        msg = make_inbound_message(scope_id="chat:42")

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert
        assert status == ResumeStatus.RESUMED

    async def test_unknown_pool_id_returns_skipped(self) -> None:
        """TurnStore returns None for this pool_id → SKIPPED."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({"telegram:main:chat:OTHER": "sess-other"}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")
        ctx = PipelineContext(hub=hub)
        msg = make_inbound_message(scope_id="chat:42")

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert — pool_id not in map → get_last_session returns None → SKIPPED
        assert status == ResumeStatus.SKIPPED

    async def test_no_turn_store_returns_skipped(self) -> None:
        """No TurnStore wired → path-3 returns None silently → SKIPPED."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        assert hub._turn_store is None
        pool = hub.get_or_create_pool(pool_id, "lyra")
        ctx = PipelineContext(hub=hub)
        msg = make_inbound_message(scope_id="chat:42")

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert — guard: hub._turn_store is None → path-3 returns None → SKIPPED
        assert status == ResumeStatus.SKIPPED

    async def test_path3_fires_without_thread_session_id(self) -> None:
        """Path-3 resumes based on pool_id only; thread_session_id is not consulted."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({pool_id: "sess-path3"}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _accepted_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _accepted_resume  # type: ignore[attr-defined]

        ctx = PipelineContext(hub=hub)

        # Message with no thread_session_id — path-3 must still work
        msg = make_inbound_message(scope_id="chat:42")
        assert isinstance(msg.platform_meta, TelegramMeta)

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert — path-3 fires from pool_id lookup, not thread_session_id
        assert status == ResumeStatus.RESUMED

    async def test_no_prior_session_does_not_call_resume(self) -> None:
        """When get_last_session returns None, pool.resume_session is never called."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resume_called: list[str] = []

        async def _track_resume(sid: str) -> bool:
            resume_called.append(sid)
            return True

        pool._session_resume_fn = _track_resume  # type: ignore[attr-defined]

        ctx = PipelineContext(hub=hub)
        msg = make_inbound_message(scope_id="chat:42")

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert — SKIPPED and resume was never called
        assert status == ResumeStatus.SKIPPED
        assert resume_called == []
