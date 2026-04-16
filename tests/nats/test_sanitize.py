"""Tests for platform_meta sanitization and scope validation (issue #525).

Covers:
- sanitize_platform_meta() pure-function behaviour (allowlist, underscore strip,
  debug logging)
- Path 2 scope validation in SubmitToPoolMiddleware
"""

from __future__ import annotations

import dataclasses
import logging
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from lyra.core.stores.turn_store import TurnStore

import pytest

from lyra.core.hub.message_pipeline import ResumeStatus
from lyra.core.hub.middleware import PipelineContext
from lyra.core.hub.path_validation import resolve_context
from roxabi_nats._sanitize import PLATFORM_META_ALLOWLIST, sanitize_platform_meta
from tests.core.conftest import _make_hub, make_inbound_message

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


class _FakeTurnStore:
    """Minimal TurnStore stub for scope-validation tests.

    Callers configure *session_map* to control what get_session_pool_id returns.
    """

    def __init__(self, session_map: dict[str, str | None] | None = None) -> None:
        self._session_map: dict[str, str | None] = session_map or {}

    async def get_session_pool_id(self, session_id: str) -> str | None:
        return self._session_map.get(session_id)

    async def get_last_session(self, pid: str) -> str | None:
        return None

    async def increment_resume_count(self, sid: str) -> None:
        pass

    async def close(self) -> None:
        pass


# ---------------------------------------------------------------------------
# TestSanitizePlatformMeta — pure-function tests (no async needed)
# ---------------------------------------------------------------------------


class TestSanitizePlatformMeta:
    def test_unknown_keys_stripped(self) -> None:
        """Unknown keys are stripped; allowlisted keys are preserved."""
        # Arrange
        meta = {"chat_id": 1, "evil_key": "x"}

        # Act
        result = sanitize_platform_meta(meta)

        # Assert
        assert "evil_key" not in result
        assert result["chat_id"] == 1

    def test_underscore_keys_stripped(self) -> None:
        """Keys with a leading underscore are stripped unconditionally."""
        # Arrange
        meta = {"chat_id": 1, "_internal": "secret"}

        # Act
        result = sanitize_platform_meta(meta)

        # Assert
        assert "_internal" not in result
        assert result["chat_id"] == 1

    def test_all_9_allowlisted_keys_preserved(self) -> None:
        """All 9 keys in PLATFORM_META_ALLOWLIST survive sanitization."""
        # Arrange — build a meta dict with every allowlisted key
        meta = {k: f"val_{k}" for k in PLATFORM_META_ALLOWLIST}
        assert len(meta) == len(PLATFORM_META_ALLOWLIST)

        # Act
        result = sanitize_platform_meta(meta)

        # Assert — all 9 keys present, no extras
        assert set(result.keys()) == PLATFORM_META_ALLOWLIST

    def test_empty_dict_unchanged(self) -> None:
        """An empty dict passes through as an empty dict."""
        # Arrange
        meta: dict = {}

        # Act
        result = sanitize_platform_meta(meta)

        # Assert
        assert result == {}

    def test_stripped_keys_logged_at_debug(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Stripped key names appear in a DEBUG log record."""
        # Arrange
        meta = {"chat_id": 1, "evil_key": "x", "_internal": "y"}

        # Act
        with caplog.at_level(logging.DEBUG, logger="roxabi_nats._sanitize"):
            sanitize_platform_meta(meta)

        # Assert — at least one debug record mentions the stripped keys
        debug_messages = " ".join(r.getMessage() for r in caplog.records)
        assert "evil_key" in debug_messages
        assert "_internal" in debug_messages

    def test_no_log_when_nothing_stripped(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No log is emitted when all keys are allowlisted (no stripping needed)."""
        # Arrange
        meta = {"chat_id": 1, "is_group": False}

        # Act
        with caplog.at_level(logging.DEBUG, logger="roxabi_nats._sanitize"):
            sanitize_platform_meta(meta)

        # Assert — no debug records emitted
        assert caplog.records == []

    def test_oversized_string_value_truncated(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Oversized string values are truncated with a DEBUG log."""
        import logging as _logging

        from roxabi_nats._sanitize import MAX_META_VALUE_LEN

        # Arrange — an allowlisted key with a > cap value
        meta = {"thread_session_id": "x" * (MAX_META_VALUE_LEN + 100)}

        # Act
        with caplog.at_level(logging.DEBUG, logger="roxabi_nats._sanitize"):
            result = sanitize_platform_meta(meta)

        # Assert — truncated, not rejected; DEBUG on lyra.nats._sanitize only
        assert len(result["thread_session_id"]) == MAX_META_VALUE_LEN
        assert result["thread_session_id"] == "x" * MAX_META_VALUE_LEN
        assert any(
            "truncated" in r.getMessage()
            and r.name == "roxabi_nats._sanitize"
            and r.levelno == _logging.DEBUG
            for r in caplog.records
        )

    def test_non_scalar_value_dropped(self, caplog: pytest.LogCaptureFixture) -> None:
        """Values that are not str/int/bool are dropped (not coerced)."""
        # Arrange — list is not a scalar; dict likewise
        meta = {"thread_session_id": [1, 2, 3], "chat_id": 42}

        # Act
        with caplog.at_level(logging.DEBUG, logger="roxabi_nats._sanitize"):
            result = sanitize_platform_meta(meta)

        # Assert — non-scalar key dropped, scalar preserved, DEBUG emitted
        assert "thread_session_id" not in result
        assert result["chat_id"] == 42
        assert any("dropped non-scalar" in r.getMessage() for r in caplog.records)

    def test_non_scalar_clean_short_list_is_silent_on_scalars(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Scalars with short values emit no log records — no spurious warnings."""
        meta = {"chat_id": 42, "is_group": True}
        with caplog.at_level(logging.DEBUG, logger="roxabi_nats._sanitize"):
            sanitize_platform_meta(meta)
        assert caplog.records == []

    def test_int_and_bool_pass_through_unchanged(self) -> None:
        """int, True, and False values pass through without coercion."""
        meta = {"chat_id": 42, "is_group": True, "topic_id": False}
        result = sanitize_platform_meta(meta)
        assert result["chat_id"] == 42
        assert result["is_group"] is True
        assert result["topic_id"] is False

    def test_allowlist_values_preserved_exactly(self) -> None:
        """Values of allowlisted keys are returned verbatim."""
        # Arrange
        meta = {
            "chat_id": 42,
            "is_group": True,
            "thread_session_id": "sess-abc",
            "unknown_field": "should_vanish",
        }

        # Act
        result = sanitize_platform_meta(meta)

        # Assert
        assert result["chat_id"] == 42
        assert result["is_group"] is True
        assert result["thread_session_id"] == "sess-abc"
        assert "unknown_field" not in result


# ---------------------------------------------------------------------------
# TestNatsBusSanitization — handler-level integration
# ---------------------------------------------------------------------------


class TestNatsBusSanitization:
    """Verify sanitization fires inside the NatsBus handler closure."""

    def test_handler_sanitizes_platform_meta(self) -> None:
        """NatsBus handler strips unknown keys via dataclasses.replace."""
        from lyra.core.message import InboundMessage, Platform
        from lyra.core.trust import TrustLevel
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
            platform_meta={
                "chat_id": 123,
                "is_group": False,
                "evil_key": "should_vanish",
            },
        )
        # Simulate the handler: serialize → deserialize → sanitize
        raw = serialize(msg)
        item = deserialize(raw, InboundMessage)
        assert "evil_key" in item.platform_meta
        cleaned = dataclasses.replace(
            item,
            platform_meta=sanitize_platform_meta(item.platform_meta),
        )
        assert "evil_key" not in cleaned.platform_meta
        assert cleaned.platform_meta["chat_id"] == 123


# ---------------------------------------------------------------------------
# TestScopeValidation — Path 2 scope-check in SubmitToPoolMiddleware
# ---------------------------------------------------------------------------


class TestScopeValidation:
    """resolve_context Path 2 rejects thread_session_id from wrong pool."""

    async def test_cross_scope_thread_session_id_rejected(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Cross-scope session → SKIPPED + warning."""
        # Arrange
        pool_id = "telegram:main:chat:99"
        hub = _make_hub()
        # TurnStore says this session belongs to a *different* pool
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({"sess-1": "telegram:main:chat:OTHER"}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")
        ctx = PipelineContext(hub=hub)

        _base = make_inbound_message(scope_id="chat:99")
        msg = dataclasses.replace(
            _base, platform_meta={**_base.platform_meta, "thread_session_id": "sess-1"}
        )

        # Act
        with caplog.at_level(logging.WARNING, logger="lyra.core.hub.path_validation"):
            status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert
        assert status == ResumeStatus.SKIPPED
        warning_text = " ".join(r.getMessage() for r in caplog.records)
        assert "scope mismatch" in warning_text

    async def test_same_scope_thread_session_id_accepted(self) -> None:
        """session registered to the same pool → resume is attempted and RESUMED."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        # TurnStore says this session belongs to the *same* pool
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({"sess-live": pool_id}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")

        async def _accepted_resume(sid: str) -> bool:
            return True

        pool._session_resume_fn = _accepted_resume  # type: ignore[attr-defined]

        ctx = PipelineContext(hub=hub)

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            platform_meta={**_base.platform_meta, "thread_session_id": "sess-live"},
        )

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert
        assert status == ResumeStatus.RESUMED

    async def test_unknown_session_id_rejected(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """get_session_pool_id returns None (unknown session) → SKIPPED + warning."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        # TurnStore has no record of this session
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({}),  # empty map → None for all session_ids
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")
        ctx = PipelineContext(hub=hub)

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            platform_meta={**_base.platform_meta, "thread_session_id": "sess-ghost"},
        )

        # Act
        with caplog.at_level(logging.WARNING, logger="lyra.core.hub.path_validation"):
            status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert
        assert status == ResumeStatus.SKIPPED
        warning_text = " ".join(r.getMessage() for r in caplog.records)
        assert "scope mismatch" in warning_text

    async def test_no_turn_store_skips_path2(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """No TurnStore → cannot validate scope → SKIPPED (safe default)."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        assert hub._turn_store is None
        pool = hub.get_or_create_pool(pool_id, "lyra")
        ctx = PipelineContext(hub=hub)

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            platform_meta={**_base.platform_meta, "thread_session_id": "sess-any"},
        )

        # Act
        with caplog.at_level(logging.DEBUG, logger="lyra.core.hub.path_validation"):
            status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert
        assert status == ResumeStatus.SKIPPED
        debug_text = " ".join(r.getMessage() for r in caplog.records)
        assert "no TurnStore" in debug_text

    async def test_path2_not_triggered_without_thread_session_id(self) -> None:
        """When thread_session_id is absent, scope validation is never consulted."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        # Even with a TurnStore wired, Path 2 must not fire
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")
        ctx = PipelineContext(hub=hub)

        msg = make_inbound_message(scope_id="chat:42")
        assert msg.platform_meta.get("thread_session_id") is None

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert — no thread_session_id means Path 2 is skipped entirely
        assert status == ResumeStatus.SKIPPED

    async def test_cross_scope_does_not_resume_session(self) -> None:
        """A cross-scope session must never call pool.resume_session."""
        # Arrange
        pool_id = "telegram:main:chat:42"
        hub = _make_hub()
        hub._turn_store = cast(
            "TurnStore",
            _FakeTurnStore({"sess-cross": "telegram:main:chat:DIFFERENT"}),
        )
        pool = hub.get_or_create_pool(pool_id, "lyra")

        resume_called: list[str] = []

        async def _track_resume(sid: str) -> bool:
            resume_called.append(sid)
            return True

        pool._session_resume_fn = _track_resume  # type: ignore[attr-defined]

        ctx = PipelineContext(hub=hub)

        _base = make_inbound_message(scope_id="chat:42")
        msg = dataclasses.replace(
            _base,
            platform_meta={**_base.platform_meta, "thread_session_id": "sess-cross"},
        )

        # Act
        status = await resolve_context(msg, pool, pool_id, ctx)

        # Assert — SKIPPED and resume was never called
        assert status == ResumeStatus.SKIPPED
        assert resume_called == []
