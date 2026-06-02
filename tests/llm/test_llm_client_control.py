"""Parity / wiring tests for LlmClient control methods (Slice S5 T26).

Each of the 6 control methods on LlmClient must route through CliNatsCodec
.encode_control + the underlying transport. This file fakes the transport,
exercises each method, and asserts the bytes received equal what
CliNatsCodec.encode_control(cmd) would produce for the equivalent input.

Slice S2 reality:
- link_lyra_session / unlink_lyra_session are pure local dict mutations (no
  transport call) — mirrors the legacy CliNatsDriver behaviour. Tests assert
  the LOCAL state effect, not a network call.
- reset / resume_and_reset / switch_cwd dispatch via transport.call (need ack);
  set_turn_store delegates to codec.set_session_store.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from factory.llm.cli_nats_codec import CliNatsCodec
from factory.llm.llm_client import LlmClient
from factory.transport._result import Ok, SanitizedError
from factory.transport.worker_pool_client import WorkerPoolClient
from roxabi_contracts.cli.models import CliControlAck
from roxabi_contracts.envelope import CONTRACT_VERSION

_SUBJECT_CONTROL = "lyra.clipool.control"

# ---------------------------------------------------------------------------
# Fake transport
# ---------------------------------------------------------------------------


class _FakeTransport:
    """Minimal transport double that records call() invocations."""

    def __init__(self, call_return: Any = None) -> None:
        self._call_return = call_return
        self.call = AsyncMock(return_value=call_return)
        self.publish = AsyncMock()

    def open_inbox(self) -> None:
        raise NotImplementedError("not used by control plane")


def _make_client(
    fake_transport: _FakeTransport,
) -> tuple[LlmClient, CliNatsCodec, MagicMock]:
    """Build LlmClient(WorkerPoolClient(_FakeTransport), CliNatsCodec()).

    WorkerPoolClient is mocked at the pool level (spec=WorkerPoolClient) so
    that its _transport attribute is the fake. This matches the wiring in
    production where reset/switch_cwd/resume_and_reset bypass the pool
    routing layer and call pool._transport.call directly.
    """
    pool = MagicMock(spec=WorkerPoolClient)
    pool._transport = fake_transport
    codec = CliNatsCodec()
    client = LlmClient(pool, codec)
    return client, codec, pool


# ---------------------------------------------------------------------------
# set_turn_store
# ---------------------------------------------------------------------------


class TestSetTurnStore:
    def test_set_turn_store_stores_locally(self) -> None:
        # Arrange
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)
        store = MagicMock()

        # Act
        client.set_turn_store(store)

        # Assert — LlmClient stores the read-side reference locally.
        assert client._turn_store is store

    def test_set_turn_store_no_transport_call(self) -> None:
        """set_turn_store must not emit any network call."""
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)
        store = MagicMock()

        client.set_turn_store(store)

        fake_transport.call.assert_not_awaited()
        fake_transport.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# link_lyra_session / unlink_lyra_session  (pure local dict mutations)
# ---------------------------------------------------------------------------


class TestLinkUnlinkLyraSession:
    def test_link_stores_mapping_in_lyra_sessions(self) -> None:
        # Arrange
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)

        # Act
        client.link_lyra_session("pool-1", "lyra-session-abc")

        # Assert
        assert client._lyra_sessions["pool-1"] == "lyra-session-abc"

    def test_link_does_not_call_transport(self) -> None:
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)

        client.link_lyra_session("pool-1", "lyra-session-abc")

        fake_transport.call.assert_not_awaited()
        fake_transport.publish.assert_not_awaited()

    def test_unlink_removes_mapping(self) -> None:
        # Arrange
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)
        client.link_lyra_session("pool-1", "lyra-session-abc")

        # Act
        client.unlink_lyra_session("pool-1")

        # Assert — key removed
        assert "pool-1" not in client._lyra_sessions

    def test_unlink_missing_key_is_a_noop(self) -> None:
        """unlink on an unknown pool_id must not raise."""
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)

        # Should not raise
        client.unlink_lyra_session("nonexistent-pool")

    def test_unlink_does_not_call_transport(self) -> None:
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)
        client.link_lyra_session("pool-1", "lyra-session-abc")

        client.unlink_lyra_session("pool-1")

        fake_transport.call.assert_not_awaited()
        fake_transport.publish.assert_not_awaited()


# ---------------------------------------------------------------------------
# reset
# ---------------------------------------------------------------------------


class TestReset:
    @pytest.mark.asyncio
    async def test_reset_calls_transport_with_codec_encoded_payload(self) -> None:
        # Arrange
        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, _, _ = _make_client(fake_transport)

        # Act
        await client.reset("pool-99")

        # Assert — transport.call was invoked once
        fake_transport.call.assert_awaited_once()
        call_args = fake_transport.call.call_args
        subject_used, payload_used = call_args.args[0], call_args.args[1]

        # Subject must match _SUBJECT_CONTROL
        assert subject_used == _SUBJECT_CONTROL

        # Decode both sides and compare fields (trace_id and issued_at differ)
        received = json.loads(payload_used)
        assert received["op"] == "reset"
        assert received["pool_id"] == "pool-99"
        assert received["contract_version"] == CONTRACT_VERSION

    @pytest.mark.asyncio
    async def test_reset_payload_is_valid_encode_control_output(self) -> None:
        """Payload bytes must be decodable by CliNatsCodec.encode_control."""
        from roxabi_contracts.cli.models import CliControlCmd

        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, _, _ = _make_client(fake_transport)

        await client.reset("pool-42")

        payload_used = fake_transport.call.call_args.args[1]
        # Must parse as CliControlCmd without error
        parsed = CliControlCmd.model_validate_json(payload_used)
        assert parsed.op == "reset"
        assert parsed.pool_id == "pool-42"


# ---------------------------------------------------------------------------
# switch_cwd
# ---------------------------------------------------------------------------


class TestSwitchCwd:
    @pytest.mark.asyncio
    async def test_switch_cwd_calls_transport_with_codec_encoded_payload(self) -> None:
        # Arrange
        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, _, _ = _make_client(fake_transport)
        target_cwd = Path("/home/user/project")

        # Act
        await client.switch_cwd("pool-7", target_cwd)

        # Assert
        fake_transport.call.assert_awaited_once()
        subject_used, payload_used = (
            fake_transport.call.call_args.args[0],
            fake_transport.call.call_args.args[1],
        )
        assert subject_used == _SUBJECT_CONTROL

        received = json.loads(payload_used)
        assert received["op"] == "switch_cwd"
        assert received["pool_id"] == "pool-7"
        assert received["cwd"] == str(target_cwd)
        assert received["contract_version"] == CONTRACT_VERSION

    @pytest.mark.asyncio
    async def test_switch_cwd_payload_parses_as_cli_control_cmd(self) -> None:
        from roxabi_contracts.cli.models import CliControlCmd

        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, _, _ = _make_client(fake_transport)

        await client.switch_cwd("pool-7", Path("/tmp/test"))

        payload_used = fake_transport.call.call_args.args[1]
        parsed = CliControlCmd.model_validate_json(payload_used)
        assert parsed.op == "switch_cwd"
        assert parsed.cwd == "/tmp/test"


# ---------------------------------------------------------------------------
# resume_and_reset — True branch (cli_session_id found, ack.resumed=True)
# ---------------------------------------------------------------------------


class TestResumeAndReset:
    def _make_ack_bytes(self, *, resumed: bool, pool_id: str = "pool-5") -> bytes:
        ack = CliControlAck(
            contract_version=CONTRACT_VERSION,
            trace_id="trace-ack",
            issued_at=datetime.now(timezone.utc),
            pool_id=pool_id,
            ok=True,
            resumed=resumed,
        )
        return ack.model_dump_json(exclude_none=True).encode()

    @pytest.mark.asyncio
    async def test_resume_and_reset_returns_true_when_ack_resumed_true(self) -> None:
        # Arrange
        ack_bytes = self._make_ack_bytes(resumed=True, pool_id="pool-5")
        fake_transport = _FakeTransport(call_return=Ok(ack_bytes))
        client, _, _ = _make_client(fake_transport)

        # Wire a turn store that returns a known cli_session_id
        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value="cli-sess-123")
        client.set_turn_store(store)

        # Act
        result = await client.resume_and_reset("pool-5", "lyra-session-xyz")

        # Assert
        assert result is True
        fake_transport.call.assert_awaited_once()

        subject_used, payload_used = (
            fake_transport.call.call_args.args[0],
            fake_transport.call.call_args.args[1],
        )
        assert subject_used == _SUBJECT_CONTROL
        received = json.loads(payload_used)
        assert received["op"] == "resume_and_reset"
        assert received["pool_id"] == "pool-5"
        assert received["session_id"] == "cli-sess-123"

    @pytest.mark.asyncio
    async def test_resume_and_reset_returns_false_when_ack_resumed_false(self) -> None:
        # Arrange
        ack_bytes = self._make_ack_bytes(resumed=False, pool_id="pool-5")
        fake_transport = _FakeTransport(call_return=Ok(ack_bytes))
        client, _, _ = _make_client(fake_transport)

        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value="cli-sess-456")
        client.set_turn_store(store)

        # Act
        result = await client.resume_and_reset("pool-5", "lyra-session-xyz")

        # Assert
        assert result is False

    @pytest.mark.asyncio
    async def test_resume_and_reset_returns_false_when_no_cli_session(self) -> None:
        """When TurnStore returns None, no transport call is made."""
        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, _, _ = _make_client(fake_transport)

        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value=None)
        client.set_turn_store(store)

        result = await client.resume_and_reset("pool-5", "lyra-session-xyz")

        assert result is False
        # No network call because cli_session_id was not found
        fake_transport.call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_and_reset_returns_false_when_no_turn_store(self) -> None:
        """When no TurnStore is wired, resume returns False without transport call."""
        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, _, _ = _make_client(fake_transport)
        # No set_turn_store call — _turn_store is None

        result = await client.resume_and_reset("pool-5", "lyra-session-xyz")

        assert result is False
        fake_transport.call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_resume_and_reset_returns_false_on_transport_err(self) -> None:
        """When transport returns Err, resume_and_reset returns False."""
        from factory.transport._result import Err

        err = SanitizedError(
            code="transport.timeout", message="TimeoutError", retryable=True
        )
        fake_transport = _FakeTransport(call_return=Err(err))
        client, _, _ = _make_client(fake_transport)

        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value="cli-sess-789")
        client.set_turn_store(store)

        result = await client.resume_and_reset("pool-5", "lyra-session-xyz")

        assert result is False
