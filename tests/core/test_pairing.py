"""Tests for the unified pairing system (issue #103 + #245 S3).

Covers test domains:
  - Core PairingManager (TestPairingConfig, TestGenerateCode, TestValidateCode,
    TestGrantAfterPairing, TestRateLimiting)
  - Plugin handlers (TestCmdInvite, TestCmdJoin, TestCmdUnpair)

Note: TestHubGate removed — trust is now resolved at adapter level via AuthStore
(see #245 S4). TestIsPaired replaced by TestGrantAfterPairing.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from lyra.commands.pairing.handlers import cmd_invite, cmd_join, cmd_unpair

# This import will fail until S1 is implemented — expected in RED phase.
from lyra.core.auth_store import AuthStore
from lyra.core.message import (
    InboundMessage,
    Platform,
)
from lyra.core.pairing import (
    PairingConfig,
    PairingError,
    PairingManager,
    _sha256,
    set_pairing_manager,
)
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_ADMIN_ID = "admin-user-1"
_USER_ID = "regular-user-1"

# Track PairingManagers and AuthStores created in tests for cleanup
_open_managers: list[PairingManager] = []
_open_stores: list[AuthStore] = []


@pytest.fixture(autouse=True)
async def _cleanup_pairing_state(tmp_path: Path):
    """Reset module-level global and close all PairingManagers/AuthStores after each test."""  # noqa: E501
    # Store tmp_path on the fixture so make_pm can use it
    _cleanup_pairing_state.tmp_path = tmp_path  # type: ignore[attr-defined]
    yield
    set_pairing_manager(None)
    for pm in _open_managers:
        await pm.close()
    _open_managers.clear()
    for store in _open_stores:
        await store.close()
    _open_stores.clear()


def make_message(  # noqa: PLR0913 — test factory with optional overrides
    content: str = "hello",
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = _USER_ID,
    is_group: bool = False,
    guild_id: int | None = None,
    *,
    is_admin: bool = False,
) -> InboundMessage:
    """Build a minimal InboundMessage for testing."""
    if platform == Platform.DISCORD:
        scope = "channel:1"
        meta = {
            "guild_id": guild_id,
            "channel_id": 1,
            "message_id": 1,
            "thread_id": None,
            "channel_type": "text",
        }
    else:
        scope = "chat:42"
        meta = {
            "chat_id": 42,
            "topic_id": None,
            "message_id": None,
            "is_group": is_group,
        }

    return InboundMessage(
        id="msg-test-1",
        platform=platform.value,
        bot_id=bot_id,
        scope_id=scope,
        user_id=user_id,
        user_name="Tester",
        is_mention=False,
        text=content,
        text_raw=content,
        timestamp=datetime.now(timezone.utc),
        platform_meta=meta,
        trust_level=TrustLevel.TRUSTED,
        is_admin=is_admin,
    )


async def make_auth_store(db_path: str = ":memory:") -> AuthStore:
    """Build and connect a real AuthStore."""
    store = AuthStore(db_path=db_path)
    await store.connect()
    _open_stores.append(store)
    return store


async def make_pm(  # noqa: PLR0913 — test factory with optional overrides
    enabled: bool = True,
    admin_user_ids: set[str] | None = None,
    max_pending: int = 3,
    rate_limit_attempts: int = 5,
    rate_limit_window: int = 300,
    session_max_age_days: int = 30,
    ttl_seconds: int = 3600,
    auth_store: AuthStore | None = None,
) -> PairingManager:
    """Build and connect a PairingManager backed by an in-memory SQLite DB.

    If auth_store is not provided, a new in-memory AuthStore is created.
    """
    if auth_store is None:
        auth_store = await make_auth_store()

    config = PairingConfig(
        enabled=enabled,
        max_pending=max_pending,
        rate_limit_attempts=rate_limit_attempts,
        rate_limit_window=rate_limit_window,
        session_max_age_days=session_max_age_days,
        ttl_seconds=ttl_seconds,
    )
    pm = PairingManager(
        config=config,
        db_path=":memory:",
        admin_user_ids=admin_user_ids or {_ADMIN_ID},
        auth_store=auth_store,
    )
    await pm.connect()
    _open_managers.append(pm)
    return pm


# ---------------------------------------------------------------------------
# TestPairingConfig
# ---------------------------------------------------------------------------


class TestPairingConfig:
    """PairingConfig.from_dict() — AC4."""

    def test_from_dict_with_empty_dict_uses_defaults(self) -> None:
        cfg = PairingConfig.from_dict({})
        assert cfg.enabled is False
        assert cfg.code_length == 8
        assert cfg.ttl_seconds == 3600
        assert cfg.max_pending == 3
        assert cfg.session_max_age_days == 30
        assert cfg.rate_limit_attempts == 5
        assert cfg.rate_limit_window == 300

    def test_from_dict_with_overrides(self) -> None:
        cfg = PairingConfig.from_dict(
            {
                "enabled": True,
                "code_length": 12,
                "ttl_seconds": 7200,
                "max_pending": 5,
                "session_max_age_days": 60,
                "rate_limit_attempts": 10,
                "rate_limit_window": 600,
            }
        )
        assert cfg.enabled is True
        assert cfg.code_length == 12
        assert cfg.ttl_seconds == 7200
        assert cfg.max_pending == 5
        assert cfg.session_max_age_days == 60
        assert cfg.rate_limit_attempts == 10
        assert cfg.rate_limit_window == 600

    def test_from_dict_ignores_unknown_keys(self) -> None:
        # Should not raise; unknown keys are silently ignored.
        cfg = PairingConfig.from_dict({"unknown_key": "value", "enabled": True})
        assert cfg.enabled is True


# ---------------------------------------------------------------------------
# TestGenerateCode
# ---------------------------------------------------------------------------


class TestPairingManagerConnect:
    """PairingManager.connect() — DB schema assertions (SC7)."""

    async def test_paired_sessions_table_not_created(self) -> None:
        """paired_sessions must not exist — replaced by AuthStore.grants (SC7)."""
        pm = await make_pm()
        assert pm._db is not None
        async with pm._db.execute(
            "SELECT name FROM sqlite_master"
            " WHERE type='table' AND name='paired_sessions'"
        ) as cur:
            row = await cur.fetchone()
        assert row is None, (
            "paired_sessions table must not be created (removed in #245)"
        )

    async def test_pairing_codes_table_exists(self) -> None:
        """pairing_codes table must still exist after connect()."""
        pm = await make_pm()
        assert pm._db is not None
        async with pm._db.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='pairing_codes'"
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, "pairing_codes table must still be created"


# ---------------------------------------------------------------------------
# TestGenerateCode
# ---------------------------------------------------------------------------


class TestGenerateCode:
    """generate_code() — AC1, AC10."""

    async def test_code_has_correct_length(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        assert len(code) == pm.config.code_length

    async def test_code_uses_safe_alphabet(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        for ch in code:
            assert ch in pm.config.alphabet, f"char {ch!r} not in safe alphabet"

    async def test_code_hash_stored_in_db(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        code_hash = _sha256(code)
        assert pm._db is not None
        async with pm._db.execute(
            "SELECT code_hash FROM pairing_codes WHERE code_hash = ?", (code_hash,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None, "SHA-256 hash not stored in DB"

    async def test_plaintext_not_stored(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        assert pm._db is not None
        async with pm._db.execute(
            "SELECT code_hash FROM pairing_codes WHERE code_hash = ?", (code,)
        ) as cur:
            row = await cur.fetchone()
        assert row is None, "Plaintext code must not appear in DB"

    async def test_max_pending_enforced(self) -> None:
        pm = await make_pm(max_pending=2)
        await pm.generate_code(_ADMIN_ID)
        await pm.generate_code(_ADMIN_ID)
        with pytest.raises(PairingError, match="Max pending"):
            await pm.generate_code(_ADMIN_ID)

    async def test_max_pending_counts_only_non_expired(self) -> None:
        # A pending code that has already expired should NOT block new codes.
        pm = await make_pm(max_pending=1, ttl_seconds=1)
        code = await pm.generate_code(_ADMIN_ID)
        # Manually expire the existing code in the DB
        assert pm._db is not None
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        await pm._db.execute(
            "UPDATE pairing_codes SET expires_at = ? WHERE code_hash = ?",
            (past, _sha256(code)),
        )
        await pm._db.commit()
        # Now generating another code should succeed because old one is expired
        new_code = await pm.generate_code(_ADMIN_ID)
        assert len(new_code) == pm.config.code_length


# ---------------------------------------------------------------------------
# TestValidateCode
# ---------------------------------------------------------------------------


class TestValidateCode:
    """validate_code() — AC2."""

    async def test_valid_code_returns_true_and_creates_session(self) -> None:
        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        code = await pm.generate_code(_ADMIN_ID)
        success, msg = await pm.validate_code(code, _USER_ID)
        assert success is True
        assert "paired" in msg.lower()
        # Grant must exist in AuthStore (replaces is_paired check)
        assert store.check(_USER_ID) == TrustLevel.TRUSTED

    async def test_invalid_code_returns_false(self) -> None:
        pm = await make_pm()
        success, msg = await pm.validate_code("BADCODE1", _USER_ID)
        assert success is False
        assert "invalid" in msg.lower()

    async def test_expired_code_returns_false(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        # Manually expire the code
        assert pm._db is not None
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        await pm._db.execute(
            "UPDATE pairing_codes SET expires_at = ? WHERE code_hash = ?",
            (past, _sha256(code)),
        )
        await pm._db.commit()
        success, msg = await pm.validate_code(code, _USER_ID)
        assert success is False
        assert "expired" in msg.lower()

    async def test_used_code_is_deleted_after_validation(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        code_hash = _sha256(code)
        await pm.validate_code(code, _USER_ID)
        assert pm._db is not None
        async with pm._db.execute(
            "SELECT id FROM pairing_codes WHERE code_hash = ?", (code_hash,)
        ) as cur:
            row = await cur.fetchone()
        assert row is None, "Used code should be deleted"

    async def test_already_paired_user_gets_session_replaced(self) -> None:
        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        code1 = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code1, _USER_ID)

        # Grant must exist after first pairing
        assert store.check(_USER_ID) == TrustLevel.TRUSTED

        # Pair again with a second code — should succeed (upsert)
        code2 = await pm.generate_code(_ADMIN_ID)
        success, _ = await pm.validate_code(code2, _USER_ID)
        assert success is True

        # Only one grant row should exist (UNIQUE constraint on identity_key)
        assert store._db is not None
        async with store._db.execute(
            "SELECT COUNT(*) FROM grants WHERE identity_key = ?",
            (_USER_ID,),
        ) as cur:
            count_row = await cur.fetchone()
        assert count_row is not None and count_row[0] == 1

        # User should still be TRUSTED
        assert store.check(_USER_ID) == TrustLevel.TRUSTED


# ---------------------------------------------------------------------------
# TestGrantAfterPairing (replaces TestIsPaired — #245 S3)
# ---------------------------------------------------------------------------


class TestGrantAfterPairing:
    """After validate_code() succeeds, AuthStore grants TRUSTED.
    After revoke_session(), AuthStore.check() returns default.

    is_paired() is removed in S3 — trust is now read from AuthStore.
    """

    async def test_validate_code_grant_has_correct_expiry(self) -> None:
        """Grant written to AuthStore must expire ~session_max_age_days from now."""
        from datetime import timezone as _tz

        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        code = await pm.generate_code(_ADMIN_ID)
        before = datetime.now(_tz.utc)
        await pm.validate_code(code, _USER_ID)
        after = datetime.now(_tz.utc)

        # Read the grant's expires_at from the DB
        assert store._db is not None
        async with store._db.execute(
            "SELECT expires_at FROM grants WHERE identity_key = ?", (_USER_ID,)
        ) as cur:
            row = await cur.fetchone()
        assert row is not None
        expires_at = datetime.fromisoformat(row[0])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=_tz.utc)

        expected_days = pm.config.session_max_age_days
        assert (expires_at - before).days >= expected_days - 1
        assert (expires_at - after).days <= expected_days + 1

    async def test_unpaired_user_returns_default_from_store(self) -> None:
        store = await make_auth_store()
        # "unknown-user" was never paired — should return PUBLIC (store default)
        assert store.check("unknown-user") == TrustLevel.PUBLIC

    async def test_revoke_session_returns_true_when_found(self) -> None:
        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        found = await pm.revoke_session(_USER_ID)
        assert found is True

    async def test_revoke_session_after_pairing_store_returns_default(self) -> None:
        """revoke_session() → auth_store.check() returns PUBLIC (default)."""
        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        # Confirm TRUSTED first
        assert store.check(_USER_ID) == TrustLevel.TRUSTED
        # Revoke
        await pm.revoke_session(_USER_ID)
        # Must be back to default
        assert store.check(_USER_ID) == TrustLevel.PUBLIC

    async def test_revoke_session_returns_false_when_not_found(self) -> None:
        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        found = await pm.revoke_session("nobody")
        assert found is False

    async def test_revoke_session_returns_false_when_no_auth_store(self) -> None:
        """revoke_session() returns False when no auth_store is configured."""
        pm = await make_pm(auth_store=None)
        found = await pm.revoke_session(_USER_ID)
        assert found is False


# ---------------------------------------------------------------------------
# TestRateLimiting
# ---------------------------------------------------------------------------


class TestRateLimiting:
    """check_rate_limit() + record_failed_attempt() — AC9."""

    def test_under_limit_returns_true(self) -> None:
        pm = PairingManager(
            config=PairingConfig(rate_limit_attempts=5, rate_limit_window=300),
            db_path=":memory:",
            admin_user_ids=set(),
            auth_store=None,
        )
        for _ in range(4):
            assert pm.check_rate_limit(_USER_ID) is True
            pm.record_failed_attempt(_USER_ID)

    def test_at_limit_returns_false(self) -> None:
        pm = PairingManager(
            config=PairingConfig(rate_limit_attempts=3, rate_limit_window=300),
            db_path=":memory:",
            admin_user_ids=set(),
            auth_store=None,
        )
        for _ in range(3):
            pm.record_failed_attempt(_USER_ID)
        assert pm.check_rate_limit(_USER_ID) is False

    def test_window_expiry_resets_limit(self) -> None:
        pm = PairingManager(
            config=PairingConfig(rate_limit_attempts=3, rate_limit_window=1),
            db_path=":memory:",
            admin_user_ids=set(),
            auth_store=None,
        )
        # Fill the window
        for _ in range(3):
            pm.record_failed_attempt(_USER_ID)
        assert pm.check_rate_limit(_USER_ID) is False

        # Manually age the timestamps past the window
        old_time = time.monotonic() - 2  # 2 seconds ago (window is 1s)
        from collections import deque

        pm._rate_timestamps[_USER_ID] = deque([old_time, old_time, old_time])

        # After window expiry, should be allowed again
        assert pm.check_rate_limit(_USER_ID) is True


# ---------------------------------------------------------------------------
# TestCmdInvite
# ---------------------------------------------------------------------------


class TestCmdInvite:
    """cmd_invite handler — AC5, AC10."""

    async def test_non_admin_is_rejected(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/invite", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_invite(msg, pool, [])
        assert "admin-only" in response.content.lower()

    async def test_admin_gets_code(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/invite", user_id=_ADMIN_ID, is_admin=True)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_invite(msg, pool, [])
        assert "Pairing code:" in response.content

    async def test_max_pending_blocks_invite(self) -> None:
        pm = await make_pm(max_pending=1)
        set_pairing_manager(pm)
        # Fill max_pending
        await pm.generate_code(_ADMIN_ID)
        msg = make_message(content="/invite", user_id=_ADMIN_ID, is_admin=True)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_invite(msg, pool, [])
        assert "max pending" in response.content.lower()

    async def test_returns_not_enabled_when_disabled(self) -> None:
        pm = await make_pm(enabled=False)
        set_pairing_manager(pm)
        msg = make_message(content="/invite", user_id=_ADMIN_ID, is_admin=True)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_invite(msg, pool, [])
        assert "not enabled" in response.content.lower()

    async def test_returns_not_enabled_when_no_manager(self) -> None:
        set_pairing_manager(None)
        msg = make_message(content="/invite", user_id=_ADMIN_ID, is_admin=True)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_invite(msg, pool, [])
        assert "not enabled" in response.content.lower()


# ---------------------------------------------------------------------------
# TestCmdJoin
# ---------------------------------------------------------------------------


class TestCmdJoin:
    """cmd_join handler — AC6, AC9."""

    async def test_valid_code_creates_session(self) -> None:
        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        set_pairing_manager(pm)
        code = await pm.generate_code(_ADMIN_ID)
        msg = make_message(content=f"/join {code}", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_join(msg, pool, [code])
        assert "paired" in response.content.lower()
        # is_paired() removed — check AuthStore grant instead
        assert store.check(_USER_ID) == TrustLevel.TRUSTED

    async def test_invalid_code_returns_error(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/join XXXXXXXX", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_join(msg, pool, ["XXXXXXXX"])
        assert "invalid" in response.content.lower()

    async def test_no_args_returns_usage(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/join", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_join(msg, pool, [])
        assert "usage" in response.content.lower()

    async def test_rate_limited_after_failures(self) -> None:
        pm = await make_pm(rate_limit_attempts=3, rate_limit_window=300)
        set_pairing_manager(pm)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        # Exhaust the rate limit with failed attempts
        for _ in range(3):
            pm.record_failed_attempt(_USER_ID)
        msg = make_message(content="/join BADCODE1", user_id=_USER_ID)
        response = await cmd_join(msg, pool, ["BADCODE1"])
        assert "too many" in response.content.lower()

    async def test_returns_not_enabled_when_disabled(self) -> None:
        pm = await make_pm(enabled=False)
        set_pairing_manager(pm)
        msg = make_message(content="/join CODE", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_join(msg, pool, ["CODE"])
        assert "not enabled" in response.content.lower()

    async def test_returns_not_enabled_when_no_manager(self) -> None:
        set_pairing_manager(None)
        msg = make_message(content="/join CODE", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_join(msg, pool, ["CODE"])
        assert "not enabled" in response.content.lower()


# ---------------------------------------------------------------------------
# TestCmdUnpair
# ---------------------------------------------------------------------------


class TestCmdUnpair:
    """cmd_unpair handler — AC7."""

    async def test_non_admin_is_rejected(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/unpair", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_unpair(msg, pool, [_USER_ID])
        assert "admin-only" in response.content.lower()

    async def test_unpair_success(self) -> None:
        store = await make_auth_store()
        pm = await make_pm(auth_store=store)
        set_pairing_manager(pm)
        # Pair the user first
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        msg = make_message(
            content=f"/unpair {_USER_ID}", user_id=_ADMIN_ID, is_admin=True
        )
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_unpair(msg, pool, [_USER_ID])
        assert "revoked" in response.content.lower()
        # is_paired() removed — check AuthStore grant instead
        assert store.check(_USER_ID) == TrustLevel.PUBLIC

    async def test_unpair_not_found(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/unpair nobody", user_id=_ADMIN_ID, is_admin=True)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_unpair(msg, pool, ["nobody"])
        assert "no paired session found" in response.content.lower()

    async def test_unpair_no_args_returns_usage(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/unpair", user_id=_ADMIN_ID, is_admin=True)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_unpair(msg, pool, [])
        assert "usage" in response.content.lower()

    async def test_returns_not_enabled_when_disabled(self) -> None:
        pm = await make_pm(enabled=False)
        set_pairing_manager(pm)
        msg = make_message(content="/unpair", user_id=_ADMIN_ID, is_admin=True)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_unpair(msg, pool, [])
        assert "not enabled" in response.content.lower()


# TestHubGate removed — hub pairing gate is removed in S4.
# Trust is now fully resolved at adapter level via AuthStore (see #245 S4).

# TestIsGroupMessage removed — _is_group_message helper was only used by the
# pairing gate which is removed in S4.
