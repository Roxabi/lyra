"""Tests for pairing command handlers (issue #103 + #245 S3).

Covers: TestCmdInvite, TestCmdJoin, TestCmdUnpair.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from lyra.commands.pairing.handlers import cmd_invite, cmd_join, cmd_unpair
from lyra.core.pairing import set_pairing_manager
from lyra.core.pool import Pool
from lyra.core.trust import TrustLevel

from .conftest import (
    _PAIRING_ADMIN_ID as _ADMIN_ID,
)
from .conftest import (
    _PAIRING_USER_ID as _USER_ID,
)
from .conftest import (
    make_pairing_auth_store as make_auth_store,
)
from .conftest import (
    make_pairing_message as make_message,
)
from .conftest import (
    make_pairing_pm as make_pm,
)

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

    async def test_formerly_admin_id_without_flag_is_rejected(self) -> None:
        """Regression: admin gate is msg.is_admin only — user_id alone is not enough."""
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/invite", user_id=_ADMIN_ID, is_admin=False)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_invite(msg, pool, [])
        assert "admin-only" in response.content.lower()


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

    async def test_formerly_admin_id_without_flag_is_rejected(self) -> None:
        """Regression: admin gate is msg.is_admin only — user_id alone is not enough."""
        pm = await make_pm()
        set_pairing_manager(pm)
        msg = make_message(content="/unpair", user_id=_ADMIN_ID, is_admin=False)
        pool = Pool(pool_id="test", agent_name="test", ctx=MagicMock())
        response = await cmd_unpair(msg, pool, [_ADMIN_ID])
        assert "admin-only" in response.content.lower()
