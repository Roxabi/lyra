"""Tests for CliPool.resume_direct + _dispatch_control resume_and_reset (#1008).

Covers the worker-side resume path where the hub driver resolves cli_session_id
and passes it directly — no TurnStore lookup on the worker side.

AAA structure throughout.
asyncio_mode = "auto" is configured project-wide in pyproject.toml.
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from factory.core.agent.agent_config import ModelConfig
from factory.core.cli.cli_pool import CliPool, CliPoolDeps
from factory.core.cli.cli_pool_worker import _ProcessEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

# A valid UUID-format session ID accepted by SESSION_ID_RE.
_VALID_CLI_SID = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
_ANOTHER_CLI_SID = "11111111-2222-3333-4444-555555555555"


def _make_pool() -> CliPool:
    """Return a CliPool with no background tasks started."""
    return CliPool(CliPoolDeps(idle_ttl=300))


def _make_live_entry(
    session_id: str = _VALID_CLI_SID, pool_id: str = "pool-1"
) -> _ProcessEntry:
    """Return a _ProcessEntry with a mock proc that reports is_alive() == True."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.returncode = None  # process is running

    model_cfg = ModelConfig(backend="claude-cli")
    entry = _ProcessEntry(
        proc=proc,
        pool_id=pool_id,
        model_config=model_cfg,
        system_prompt="",
        session_id=session_id,
        resumed_from=None,
    )
    return entry


def _make_dead_entry(pool_id: str = "pool-1") -> _ProcessEntry:
    """Return a _ProcessEntry with a mock proc that reports is_alive() == False."""
    proc = MagicMock(spec=asyncio.subprocess.Process)
    proc.returncode = 1  # process exited

    model_cfg = ModelConfig(backend="claude-cli")
    entry = _ProcessEntry(
        proc=proc,
        pool_id=pool_id,
        model_config=model_cfg,
        system_prompt="",
        resumed_from=None,
    )
    return entry


def _control_payload(**overrides: object) -> dict:
    """Build a minimal CliControlCmd dict."""
    base: dict = {
        "contract_version": "1",
        "trace_id": "trace-002",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "pool_id": "pool-1",
        "op": "resume_and_reset",
        "session_id": _VALID_CLI_SID,
    }
    base.update(overrides)
    return base


def _make_nats_msg(subject: str = "lyra.clipool.control", reply: str = "_INBOX.test"):
    msg = MagicMock()
    msg.subject = subject
    msg.reply = reply
    return msg


# ---------------------------------------------------------------------------
# CliPool.resume_direct — valid session, process not alive
# ---------------------------------------------------------------------------


class TestResumedDirect:
    """CliPool.resume_direct() stores the resume session and returns True."""

    @pytest.mark.asyncio
    async def test_resume_direct_valid_session_kills_and_stores(self) -> None:
        """resume_direct with valid cli_session_id kills process and stores resume."""
        # Arrange
        pool = _make_pool()
        pool._entries["pool-1"] = _make_live_entry(_ANOTHER_CLI_SID)

        with patch.object(pool, "_kill", new_callable=AsyncMock) as mock_kill:
            # Act
            result = await pool.resume_direct("pool-1", _VALID_CLI_SID)

        # Assert — _kill was called to evict the current entry
        mock_kill.assert_awaited_once()

        # Assert — resume id stored for next spawn
        assert pool._resume_session_ids.get("pool-1") == _VALID_CLI_SID

        # Assert — returns True
        assert result is True

    @pytest.mark.asyncio
    async def test_resume_direct_already_on_session_no_kill(self) -> None:
        """resume_direct is a no-op when the live process is already on that session."""
        # Arrange
        pool = _make_pool()
        pool._entries["pool-1"] = _make_live_entry(_VALID_CLI_SID)

        with patch.object(pool, "_kill", new_callable=AsyncMock) as mock_kill:
            # Act
            result = await pool.resume_direct("pool-1", _VALID_CLI_SID)

        # Assert — no kill needed
        mock_kill.assert_not_awaited()

        # Assert — still returns True
        assert result is True

    @pytest.mark.asyncio
    async def test_resume_direct_empty_session_id_returns_false(self) -> None:
        """resume_direct returns False and does not call _kill for empty session_id."""
        # Arrange
        pool = _make_pool()

        with patch.object(pool, "_kill", new_callable=AsyncMock) as mock_kill:
            # Act
            result = await pool.resume_direct("pool-1", "")

        # Assert
        assert result is False
        mock_kill.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_direct_invalid_format_returns_false(self) -> None:
        """resume_direct returns False for a non-UUID session_id string."""
        # Arrange
        pool = _make_pool()

        with patch.object(pool, "_kill", new_callable=AsyncMock) as mock_kill:
            # Act
            result = await pool.resume_direct("pool-1", "not-a-uuid")

        # Assert
        assert result is False
        mock_kill.assert_not_awaited()


# ---------------------------------------------------------------------------
# _dispatch_control — resume_and_reset path
# ---------------------------------------------------------------------------


class TestDispatchControlResumeAndReset:
    """_dispatch_control('resume_and_reset') uses resume_direct, returns correct ACK."""

    @pytest.mark.asyncio
    async def test_dispatch_resume_and_reset_calls_resume_direct_returns_ack(
        self,
    ) -> None:
        """Valid session_id: resume_direct called, ACK has ok=True, resumed=True."""
        import json

        from factory.adapters.clipool.clipool_worker import CliPoolNatsWorker
        from roxabi_contracts.cli.models import CliControlCmd

        # Arrange
        pool = MagicMock(spec=CliPool)
        pool._entries = {}
        pool.resume_direct = AsyncMock(return_value=True)

        worker = CliPoolNatsWorker(pool)
        cmd = CliControlCmd.model_validate(
            _control_payload(session_id=_VALID_CLI_SID, op="resume_and_reset")
        )

        # Act
        ack_bytes = await worker._dispatch_control(cmd)
        ack = json.loads(ack_bytes)

        # Assert — resume_direct called with pool_id and cli_session_id
        pool.resume_direct.assert_awaited_once_with("pool-1", _VALID_CLI_SID)

        # Assert — ACK fields
        assert ack["ok"] is True
        assert ack["resumed"] is True

    @pytest.mark.asyncio
    async def test_dispatch_resume_and_reset_session_id_none_returns_ok_false(
        self,
    ) -> None:
        """Missing session_id: ACK has ok=False, resume_direct not called."""
        import json

        from factory.adapters.clipool.clipool_worker import CliPoolNatsWorker
        from roxabi_contracts.cli.models import CliControlCmd

        # Arrange
        pool = MagicMock(spec=CliPool)
        pool._entries = {}
        pool.resume_direct = AsyncMock(return_value=False)

        worker = CliPoolNatsWorker(pool)
        cmd = CliControlCmd.model_validate(
            _control_payload(session_id=None, op="resume_and_reset")
        )

        # Act
        ack_bytes = await worker._dispatch_control(cmd)
        ack = json.loads(ack_bytes)

        # Assert — resume_direct was NOT called
        pool.resume_direct.assert_not_awaited()

        # Assert — ok=False
        assert ack["ok"] is False


# ---------------------------------------------------------------------------
# Structural: no TurnStore import in clipool_worker
# ---------------------------------------------------------------------------


class TestNoTurnStoreInWorker:
    """Structural check: clipool_worker must not import TurnStore."""

    def test_clipool_worker_has_no_turn_store_reference(self) -> None:
        """grep confirms no TurnStore or turn_store symbols in clipool_worker.py."""
        import subprocess

        result = subprocess.run(
            [
                "grep",
                "-i",
                "turn_store\\|TurnStore",
                "src/factory/adapters/clipool/clipool_worker.py",
            ],
            capture_output=True,
            text=True,
            cwd=str(Path(__file__).parents[3]),
        )
        # grep returns exit code 1 when no match — that is the desired outcome
        assert result.returncode == 1, (
            f"clipool_worker.py references TurnStore:\n{result.stdout}"
        )
