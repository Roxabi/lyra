"""Parity / wiring tests for LlmClient control methods (Slice S5 T26).

Each of the 6 control methods on LlmClient must route through CliNatsCodec
.encode_control + the underlying transport. This file fakes the transport,
exercises each method, and asserts the bytes received equal what
CliNatsCodec.encode_control(cmd) would produce for the equivalent input.

Slice S2 reality:
- link_lyra_session / unlink_lyra_session are pure local dict mutations (no
  transport call) — mirrors the legacy CliNatsDriver behaviour. Tests assert
  the LOCAL state effect, not a network call.
- reset / switch_cwd dispatch via transport.call (need ack);
  queue_resume stashes cli_session_id into _pending_resume (no transport call);
  set_turn_store wires the TurnStore for cli_session_id lookups.
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
from factory.transport._result import Ok
from factory.transport.worker_pool_client import WorkerPoolClient
from roxabi_contracts.envelope import CONTRACT_VERSION

_SUBJECT_CONTROL = "factory.clipool.control"

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
# queue_resume — stash cli_session_id for next complete()/stream() call
# ---------------------------------------------------------------------------


class TestQueueResume:
    @pytest.mark.asyncio
    async def test_queue_resume_stashes_and_returns_true(self) -> None:
        """TurnStore returns cli_sid → stashed in _pending_resume, True returned."""
        # Arrange
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)

        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value="cli-sess-123")
        client.set_turn_store(store)

        # Act
        result = await client.queue_resume("pool-5", "lyra-session-xyz")

        # Assert — stashed, no transport call
        assert result is True
        assert client._pending_resume["pool-5"] == "cli-sess-123"
        fake_transport.call.assert_not_awaited()
        fake_transport.publish.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queue_resume_returns_false_no_cli_session(self) -> None:
        """TurnStore returns None → False, nothing stashed, no transport call."""
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)

        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value=None)
        client.set_turn_store(store)

        result = await client.queue_resume("pool-5", "lyra-session-xyz")

        assert result is False
        assert "pool-5" not in client._pending_resume
        fake_transport.call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_queue_resume_returns_false_no_turn_store(self) -> None:
        """No TurnStore wired → False, no transport call."""
        fake_transport = _FakeTransport()
        client, _, _ = _make_client(fake_transport)
        # No set_turn_store call — _turn_store is None

        result = await client.queue_resume("pool-5", "lyra-session-xyz")

        assert result is False
        assert "pool-5" not in client._pending_resume
        fake_transport.call.assert_not_awaited()

    @pytest.mark.asyncio
    async def test_complete_pops_pending_resume(self) -> None:
        """After queue_resume stashes, complete() consumes the stash."""
        from factory.transport._result import Err, SanitizedError

        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, codec, pool = _make_client(fake_transport)

        # Make request_with_routing return an Err (use a known code so decode succeeds)
        err = Err(SanitizedError(code="transport.no_responders", message="forced", retryable=False))
        pool.request_with_routing = AsyncMock(return_value=err)

        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value="cli-sess-abc")
        client.set_turn_store(store)

        await client.queue_resume("pool-7", "lyra-session-xyz")
        assert client._pending_resume.get("pool-7") == "cli-sess-abc"

        from factory.core.agent.agent_config import ModelConfig

        model_cfg = ModelConfig(backend="claude-cli")
        # Pop happens before request_with_routing; stash is consumed even on Err
        await client.complete("pool-7", "hello", model_cfg, "sys")

        assert "pool-7" not in client._pending_resume

    @pytest.mark.asyncio
    async def test_stream_pops_pending_resume(self) -> None:
        """After queue_resume stashes, stream() consumes it via resume_session_id."""
        fake_transport = _FakeTransport(call_return=Ok(b"{}"))
        client, codec, pool = _make_client(fake_transport)

        # stream_request must be an async generator; return empty one
        async def _empty_stream(*_args: object, **_kwargs: object):  # type: ignore[return]
            return
            yield  # make it an async generator

        pool.stream_request = _empty_stream

        store = MagicMock()
        store.get_cli_session = AsyncMock(return_value="cli-sess-def")
        client.set_turn_store(store)

        await client.queue_resume("pool-8", "lyra-session-abc")
        assert client._pending_resume.get("pool-8") == "cli-sess-def"

        from factory.core.agent.agent_config import ModelConfig

        model_cfg = ModelConfig(backend="claude-cli")
        # Exhaust the (empty) stream — stash is popped when body executes (first iteration)
        async for _ in client.stream("pool-8", "hello", model_cfg, "sys"):
            pass

        assert "pool-8" not in client._pending_resume
