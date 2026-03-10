"""Tests for the unified pairing system (issue #103).

Covers all 13 ACs across three test domains:
  - Core PairingManager (TestPairingConfig, TestGenerateCode, TestValidateCode,
    TestIsPaired, TestRateLimiting)
  - Plugin handlers (TestCmdInvite, TestCmdJoin, TestCmdUnpair)
  - Hub pairing gate (TestHubGate)
"""

from __future__ import annotations

import asyncio
import time
from datetime import datetime, timedelta, timezone

import pytest

from lyra.core.agent import Agent
from lyra.core.hub import Hub, _is_group_message
from lyra.core.message import (
    DiscordContext,
    Message,
    MessageType,
    Platform,
    Response,
    TelegramContext,
)
from lyra.core.pairing import (
    PairingConfig,
    PairingError,
    PairingManager,
    _sha256,
    set_pairing_manager,
)
from lyra.core.pool import Pool
from lyra.plugins.pairing.handlers import cmd_invite, cmd_join, cmd_unpair

# ---------------------------------------------------------------------------
# Helpers / fixtures
# ---------------------------------------------------------------------------

_ADMIN_ID = "admin-user-1"
_USER_ID = "regular-user-1"

# Track PairingManagers created in tests for cleanup
_open_managers: list[PairingManager] = []


@pytest.fixture(autouse=True)
async def _cleanup_pairing_state():
    """Reset module-level global and close all PairingManagers after each test."""
    yield
    set_pairing_manager(None)
    for pm in _open_managers:
        await pm.close()
    _open_managers.clear()


def make_message(
    content: str = "hello",
    platform: Platform = Platform.TELEGRAM,
    bot_id: str = "main",
    user_id: str = _USER_ID,
    is_group: bool = False,
    guild_id: int | None = None,
) -> Message:
    """Build a minimal Message for testing."""
    if platform == Platform.DISCORD:
        ctx: TelegramContext | DiscordContext = DiscordContext(
            guild_id=guild_id,
            channel_id=1,
            message_id=1,
        )
    else:
        ctx = TelegramContext(chat_id=42, is_group=is_group)

    return Message(
        id="msg-test-1",
        platform=platform,
        bot_id=bot_id,
        user_id=user_id,
        user_name="Tester",
        is_mention=False,
        is_from_bot=False,
        content=content,
        type=MessageType.TEXT,
        timestamp=datetime.now(timezone.utc),
        platform_context=ctx,
    )


async def make_pm(
    enabled: bool = True,
    admin_user_ids: set[str] | None = None,
    max_pending: int = 3,
    rate_limit_attempts: int = 5,
    rate_limit_window: int = 300,
    session_max_age_days: int = 30,
    ttl_seconds: int = 3600,
) -> PairingManager:
    """Build and connect a PairingManager backed by an in-memory SQLite DB."""
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
        past = (
            datetime.now(timezone.utc) - timedelta(seconds=10)
        ).isoformat()
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
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        success, msg = await pm.validate_code(code, _USER_ID)
        assert success is True
        assert "paired" in msg.lower()
        # Session must exist
        assert await pm.is_paired(_USER_ID)

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
        pm = await make_pm()
        code1 = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code1, _USER_ID)

        # Get original expiry
        assert pm._db is not None
        async with pm._db.execute(
            "SELECT expires_at FROM paired_sessions WHERE identity_key = ?",
            (_USER_ID,),
        ) as cur:
            row1 = await cur.fetchone()
        original_expires = row1[0] if row1 else None

        # Pair again with a second code
        code2 = await pm.generate_code(_ADMIN_ID)
        success, _ = await pm.validate_code(code2, _USER_ID)
        assert success is True

        async with pm._db.execute(
            "SELECT expires_at FROM paired_sessions WHERE identity_key = ?",
            (_USER_ID,),
        ) as cur:
            row2 = await cur.fetchone()
        new_expires = row2[0] if row2 else None

        # Only one row should exist (UNIQUE constraint)
        async with pm._db.execute(
            "SELECT COUNT(*) FROM paired_sessions WHERE identity_key = ?",
            (_USER_ID,),
        ) as cur:
            count_row = await cur.fetchone()
        assert count_row is not None and count_row[0] == 1

        # Expiry should be refreshed (new >= original)
        assert new_expires >= original_expires  # type: ignore[operator]


# ---------------------------------------------------------------------------
# TestIsPaired
# ---------------------------------------------------------------------------


class TestIsPaired:
    """is_paired() — AC3, AC11."""

    async def test_paired_user_returns_true(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        assert await pm.is_paired(_USER_ID) is True

    async def test_unpaired_user_returns_false(self) -> None:
        pm = await make_pm()
        assert await pm.is_paired("unknown-user") is False

    async def test_expired_session_deleted_and_returns_false(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        # Manually expire the session
        assert pm._db is not None
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        await pm._db.execute(
            "UPDATE paired_sessions SET expires_at = ? WHERE identity_key = ?",
            (past, _USER_ID),
        )
        await pm._db.commit()
        # is_paired should lazily delete and return False
        result = await pm.is_paired(_USER_ID)
        assert result is False
        # Session row should be gone
        async with pm._db.execute(
            "SELECT id FROM paired_sessions WHERE identity_key = ?", (_USER_ID,)
        ) as cur:
            row = await cur.fetchone()
        assert row is None, "Expired session should be lazily deleted"

    async def test_admin_bypass_always_returns_true(self) -> None:
        pm = await make_pm()
        # Admin is not paired via code — should still return True
        assert await pm.is_paired(_ADMIN_ID) is True

    async def test_revoke_session_returns_true_when_found(self) -> None:
        pm = await make_pm()
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        found = await pm.revoke_session(_USER_ID)
        assert found is True
        assert await pm.is_paired(_USER_ID) is False

    async def test_revoke_session_returns_false_when_not_found(self) -> None:
        pm = await make_pm()
        found = await pm.revoke_session("nobody")
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
        )
        for _ in range(4):
            assert pm.check_rate_limit(_USER_ID) is True
            pm.record_failed_attempt(_USER_ID)

    def test_at_limit_returns_false(self) -> None:
        pm = PairingManager(
            config=PairingConfig(rate_limit_attempts=3, rate_limit_window=300),
            db_path=":memory:",
            admin_user_ids=set(),
        )
        for _ in range(3):
            pm.record_failed_attempt(_USER_ID)
        assert pm.check_rate_limit(_USER_ID) is False

    def test_window_expiry_resets_limit(self) -> None:
        pm = PairingManager(
            config=PairingConfig(rate_limit_attempts=3, rate_limit_window=1),
            db_path=":memory:",
            admin_user_ids=set(),
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
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_invite(msg, pool, [])
        assert "admin-only" in response.content.lower()

    async def test_admin_gets_code(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/invite", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_invite(msg, pool, [])
        assert "Pairing code:" in response.content

    async def test_max_pending_blocks_invite(self) -> None:
        pm = await make_pm(max_pending=1)
        set_pairing_manager(pm)
        # Fill max_pending
        await pm.generate_code(_ADMIN_ID)
        msg = make_message(content="/invite", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_invite(msg, pool, [])
        assert "max pending" in response.content.lower()

    async def test_returns_not_enabled_when_disabled(self) -> None:
        pm = await make_pm(enabled=False)
        set_pairing_manager(pm)
        msg = make_message(content="/invite", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_invite(msg, pool, [])
        assert "not enabled" in response.content.lower()

    async def test_returns_not_enabled_when_no_manager(self) -> None:
        set_pairing_manager(None)
        msg = make_message(content="/invite", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_invite(msg, pool, [])
        assert "not enabled" in response.content.lower()


# ---------------------------------------------------------------------------
# TestCmdJoin
# ---------------------------------------------------------------------------


class TestCmdJoin:
    """cmd_join handler — AC6, AC9."""

    async def test_valid_code_creates_session(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        code = await pm.generate_code(_ADMIN_ID)
        msg = make_message(content=f"/join {code}", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_join(msg, pool, [code])
        assert "paired" in response.content.lower()
        assert await pm.is_paired(_USER_ID)

    async def test_invalid_code_returns_error(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/join XXXXXXXX", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_join(msg, pool, ["XXXXXXXX"])
        assert "invalid" in response.content.lower()

    async def test_no_args_returns_usage(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/join", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_join(msg, pool, [])
        assert "usage" in response.content.lower()

    async def test_rate_limited_after_failures(self) -> None:
        pm = await make_pm(rate_limit_attempts=3, rate_limit_window=300)
        set_pairing_manager(pm)
        pool = Pool(pool_id="test", agent_name="test")
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
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_join(msg, pool, ["CODE"])
        assert "not enabled" in response.content.lower()

    async def test_returns_not_enabled_when_no_manager(self) -> None:
        set_pairing_manager(None)
        msg = make_message(content="/join CODE", user_id=_USER_ID)
        pool = Pool(pool_id="test", agent_name="test")
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
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_unpair(msg, pool, [_USER_ID])
        assert "admin-only" in response.content.lower()

    async def test_unpair_success(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        # Pair the user first
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        msg = make_message(content=f"/unpair {_USER_ID}", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_unpair(msg, pool, [_USER_ID])
        assert "revoked" in response.content.lower()
        assert not await pm.is_paired(_USER_ID)

    async def test_unpair_not_found(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/unpair nobody", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_unpair(msg, pool, ["nobody"])
        assert "no paired session found" in response.content.lower()

    async def test_unpair_no_args_returns_usage(self) -> None:
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/unpair", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_unpair(msg, pool, [])
        assert "usage" in response.content.lower()

    async def test_returns_not_enabled_when_disabled(self) -> None:
        pm = await make_pm(enabled=False)
        set_pairing_manager(pm)
        msg = make_message(content="/unpair", user_id=_ADMIN_ID)
        pool = Pool(pool_id="test", agent_name="test")
        response = await cmd_unpair(msg, pool, [])
        assert "not enabled" in response.content.lower()


# ---------------------------------------------------------------------------
# TestHubGate
# ---------------------------------------------------------------------------


class TestHubGate:
    """Hub pairing gate — AC8, AC11, AC12."""

    def _make_hub(self, pm: PairingManager) -> Hub:
        hub = Hub(pairing_manager=pm)
        config = Agent(name="lyra", system_prompt="", memory_namespace="lyra")

        from lyra.core.agent import AgentBase

        class NullAgent(AgentBase):
            async def process(self, msg: Message, pool: Pool) -> Response:
                return Response(content="ok")

        agent = NullAgent(config)
        hub.register_agent(agent)
        return hub

    def _make_capturing_adapter(self) -> tuple[object, list[Response]]:
        """Return (adapter, captured_responses) pair."""
        captured: list[Response] = []

        class CapturingAdapter:
            async def send(self, original_msg: Message, response: Response) -> None:
                captured.append(response)

            async def send_streaming(
                self, original_msg: Message, chunks: object
            ) -> None:
                pass

        return CapturingAdapter(), captured

    async def _run_hub_once(self, hub: Hub, msg: Message) -> None:
        """Put a message on the bus and run the hub for one iteration."""
        await hub.bus.put(msg)
        try:
            await asyncio.wait_for(hub.run(), timeout=0.3)
        except asyncio.TimeoutError:
            pass

    async def test_unpaired_dm_gets_rejection(self) -> None:
        pm = await make_pm(enabled=True)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        msg = make_message(content="hello", user_id=_USER_ID, is_group=False)
        await self._run_hub_once(hub, msg)

        assert len(captured) == 1
        assert "not paired" in captured[0].content.lower()

    async def test_unpaired_group_message_silently_dropped(self) -> None:
        pm = await make_pm(enabled=True)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        msg = make_message(content="hello", user_id=_USER_ID, is_group=True)
        await self._run_hub_once(hub, msg)

        # No response sent for group messages from unpaired users
        assert len(captured) == 0

    async def test_join_command_passes_gate(self) -> None:
        pm = await make_pm(enabled=True)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        code = await pm.generate_code(_ADMIN_ID)
        # Set pairing manager so the plugin handler works
        set_pairing_manager(pm)

        msg = make_message(content=f"/join {code}", user_id=_USER_ID)
        await self._run_hub_once(hub, msg)

        # The /join command was routed (possibly via agent since no router on NullAgent)
        # The key assertion: no "not paired" rejection was sent
        assert len(captured) >= 1, "expected a response, not a silent drop"
        for resp in captured:
            assert "not paired" not in resp.content.lower()

    async def test_admin_bypasses_gate(self) -> None:
        pm = await make_pm(enabled=True)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        # Admin user sends a plain message — should NOT be blocked
        msg = make_message(content="hello admin", user_id=_ADMIN_ID)
        await self._run_hub_once(hub, msg)

        # Should have a response (agent processed it) — not a rejection
        assert len(captured) >= 1, "admin should not be blocked"
        for resp in captured:
            assert "not paired" not in resp.content.lower()

    async def test_gate_inactive_when_disabled(self) -> None:
        pm = await make_pm(enabled=False)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        # Unpaired user — gate should be off
        msg = make_message(content="hello", user_id=_USER_ID)
        await self._run_hub_once(hub, msg)

        # No rejection since gate is disabled
        assert len(captured) >= 1, "gate should be inactive"
        for resp in captured:
            assert "not paired" not in resp.content.lower()

    async def test_expired_session_rejected(self) -> None:
        pm = await make_pm(enabled=True)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.TELEGRAM, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.TELEGRAM, "main", "*", "lyra", "telegram:main:*")

        # Pair the user, then expire the session
        code = await pm.generate_code(_ADMIN_ID)
        await pm.validate_code(code, _USER_ID)
        assert pm._db is not None
        past = (datetime.now(timezone.utc) - timedelta(seconds=10)).isoformat()
        await pm._db.execute(
            "UPDATE paired_sessions SET expires_at = ? WHERE identity_key = ?",
            (past, _USER_ID),
        )
        await pm._db.commit()

        msg = make_message(content="hello", user_id=_USER_ID)
        await self._run_hub_once(hub, msg)

        assert len(captured) == 1
        assert "not paired" in captured[0].content.lower()

    async def test_discord_unpaired_dm_rejected(self) -> None:
        pm = await make_pm(enabled=True)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.DISCORD, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.DISCORD, "main", "*", "lyra", "discord:main:*")

        # Discord DM: guild_id=None
        msg = make_message(
            content="hello",
            user_id=_USER_ID,
            platform=Platform.DISCORD,
            guild_id=None,
        )
        await self._run_hub_once(hub, msg)

        assert len(captured) == 1
        assert "not paired" in captured[0].content.lower()

    async def test_discord_unpaired_guild_silently_dropped(self) -> None:
        pm = await make_pm(enabled=True)
        hub = self._make_hub(pm)
        adapter, captured = self._make_capturing_adapter()
        hub.register_adapter(Platform.DISCORD, "main", adapter)  # type: ignore[arg-type]
        hub.register_binding(Platform.DISCORD, "main", "*", "lyra", "discord:main:*")

        # Discord guild message: guild_id != None
        msg = make_message(
            content="hello",
            user_id=_USER_ID,
            platform=Platform.DISCORD,
            guild_id=12345,
        )
        await self._run_hub_once(hub, msg)

        # Silently dropped
        assert len(captured) == 0


# ---------------------------------------------------------------------------
# TestIsGroupMessage (helper)
# ---------------------------------------------------------------------------


class TestIsGroupMessage:
    """_is_group_message() helper — used by the hub gate."""

    def test_telegram_group(self) -> None:
        msg = make_message(is_group=True)
        assert _is_group_message(msg) is True

    def test_telegram_dm(self) -> None:
        msg = make_message(is_group=False)
        assert _is_group_message(msg) is False

    def test_discord_guild(self) -> None:
        msg = make_message(platform=Platform.DISCORD, guild_id=12345)
        assert _is_group_message(msg) is True

    def test_discord_dm(self) -> None:
        msg = make_message(platform=Platform.DISCORD, guild_id=None)
        assert _is_group_message(msg) is False
