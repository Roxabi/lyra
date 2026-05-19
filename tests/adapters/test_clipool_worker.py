"""Tests for CliPoolNatsWorker — RED phase (T9).

CliPoolNatsWorker does not exist yet; these tests will fail until T10 lands.

Coverage targets:
- handle() routing by msg.subject
- _handle_cmd(): stream=True path (send_streaming) and stream=False path (send)
- _handle_control(): reset, resume_and_reset, switch_cwd ops
- heartbeat_payload() pool_count extension
- _extra_subjects() contract
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.cli.cli_pool import CliPool, CliResult
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_nats_msg(subject: str = "lyra.clipool.cmd", reply: str = "_INBOX.test"):
    msg = MagicMock()
    msg.subject = subject
    msg.reply = reply
    return msg


async def _make_event_iter(events):
    for e in events:
        yield e


def _cmd_payload(**overrides) -> dict:
    """Build a minimal CliCmdPayload dict with required envelope fields."""
    base = {
        "contract_version": "1",
        "trace_id": "trace-001",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "pool_id": "pool-1",
        "lyra_session_id": "sess-1",
        "text": "hello",
        "model_cfg": {},
        "system_prompt": "",
        "stream": True,
    }
    base.update(overrides)
    return base


def _control_payload(**overrides) -> dict:
    """Build a minimal CliControlCmd dict with required envelope fields."""
    base = {
        "contract_version": "1",
        "trace_id": "trace-002",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "pool_id": "pool-1",
        "op": "reset",
    }
    base.update(overrides)
    return base


def _make_pool() -> MagicMock:
    pool = MagicMock(spec=CliPool)
    pool._entries = {}
    pool.send_streaming = AsyncMock()
    pool.send = AsyncMock()
    pool.reset = AsyncMock()
    pool.resume_and_reset = AsyncMock(return_value=True)
    pool.resume_direct = AsyncMock(return_value=True)
    pool.switch_cwd = AsyncMock()
    return pool


# ---------------------------------------------------------------------------
# _extra_subjects
# ---------------------------------------------------------------------------


def test_extra_subjects_includes_control() -> None:
    """_extra_subjects() returns exactly ['lyra.clipool.control']."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()

    # Act
    worker = CliPoolNatsWorker(pool)

    # Assert
    assert worker._extra_subjects() == ["lyra.clipool.control"]


# ---------------------------------------------------------------------------
# handle() routing
# ---------------------------------------------------------------------------


async def test_handle_routes_control_by_subject() -> None:
    """msg.subject == 'lyra.clipool.control' -> _handle_control; else -> _handle_cmd."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    worker = CliPoolNatsWorker(pool)

    control_msg = _make_nats_msg(subject="lyra.clipool.control")
    cmd_msg = _make_nats_msg(subject="lyra.clipool.cmd")

    with (
        patch.object(worker, "_handle_control", new_callable=AsyncMock) as mock_ctrl,
        patch.object(worker, "_handle_cmd", new_callable=AsyncMock) as mock_cmd,
    ):
        # Act — control subject
        await worker.handle(control_msg, _control_payload())
        # Act — default subject
        await worker.handle(cmd_msg, _cmd_payload())

    # Assert
    mock_ctrl.assert_awaited_once()
    mock_cmd.assert_awaited_once()


# ---------------------------------------------------------------------------
# _handle_cmd — streaming path
# ---------------------------------------------------------------------------


async def test_handle_cmd_stream_calls_pool_send_streaming() -> None:
    """stream=True: send_streaming() called, chunks published to msg.reply."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    text_event = TextLlmEvent(text="hello")
    result_event = ResultLlmEvent(is_error=False, duration_ms=0)
    pool.send_streaming.return_value = _make_event_iter([text_event, result_event])

    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(subject="lyra.clipool.cmd", reply="_INBOX.reply")
    payload = _cmd_payload(stream=True)

    # Act
    await worker._handle_cmd(msg, payload)

    # Assert — pool.send_streaming was called
    pool.send_streaming.assert_called_once()
    # Assert — at least one publish to msg.reply
    assert nc.publish.call_count >= 1
    call_subjects = [call.args[0] for call in nc.publish.call_args_list]
    assert all(s == "_INBOX.reply" for s in call_subjects)


async def test_handle_cmd_nonstream_calls_pool_send() -> None:
    """stream=False: pool.send() called, single reply published to msg.reply."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    pool.send.return_value = CliResult(result="answer", session_id="sid", error="")

    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(subject="lyra.clipool.cmd", reply="_INBOX.reply")
    payload = _cmd_payload(stream=False)

    # Act
    await worker._handle_cmd(msg, payload)

    # Assert — pool.send used, not send_streaming
    pool.send.assert_called_once()
    pool.send_streaming.assert_not_called()

    # Assert — single reply published
    nc.publish.assert_called_once()
    publish_subject = nc.publish.call_args.args[0]
    assert publish_subject == "_INBOX.reply"


async def test_handle_cmd_send_streaming_exception_publishes_error() -> None:
    """When pool.send_streaming raises, worker publishes error chunk via _nc.publish."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    pool.send_streaming = AsyncMock(side_effect=RuntimeError("boom"))
    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc
    msg = _make_nats_msg(subject="lyra.clipool.cmd", reply="_INBOX.test.1")
    payload = _cmd_payload(stream=True)

    # Act
    await worker._handle_cmd(msg, payload)

    # Assert — error published directly via _nc.publish (not self.reply)
    nc.publish.assert_awaited_once()
    call_args = nc.publish.call_args
    subject = call_args.args[0]
    data = json.loads(call_args.args[1])
    assert subject == "_INBOX.test.1"
    assert data["is_error"] is True
    assert data["done"] is True


async def test_handle_cmd_publishes_done_chunk_after_stream() -> None:
    """Streaming path publishes a terminal 'done' chunk after iterating events."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    text_event = TextLlmEvent(text="chunk1")
    pool.send_streaming.return_value = _make_event_iter([text_event])

    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(subject="lyra.clipool.cmd", reply="_INBOX.reply")

    # Act
    await worker._handle_cmd(msg, _cmd_payload(stream=True))

    # Assert — final publish carries done=True
    published_payloads = [
        json.loads(call.args[1].decode())
        for call in nc.publish.call_args_list
        if len(call.args) > 1
    ]
    assert any(p.get("done") is True for p in published_payloads)


async def test_handle_cmd_streaming_forwards_tool_use_as_keepalive() -> None:
    """ToolUseLlmEvent is forwarded as event_type='tool_use' chunk (done=False).

    Tool execution can take minutes without producing TextLlmEvents; without
    this forward, the hub-side `_stream_gen` per-chunk timer would kill the
    healthy session. The chunk acts as a keepalive — the hub ignores its
    payload but the arrival resets the timer.
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    tool_event = ToolUseLlmEvent(tool_name="Bash", tool_id="toolu_01")
    result_event = ResultLlmEvent(is_error=False, duration_ms=0)
    pool.send_streaming.return_value = _make_event_iter([tool_event, result_event])

    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(reply="_INBOX.reply")

    # Act
    await worker._handle_cmd(msg, _cmd_payload(stream=True))

    # Assert — a tool_use chunk was published with done=False
    payloads = [
        json.loads(call.args[1].decode())
        for call in nc.publish.call_args_list
        if len(call.args) > 1
    ]
    tool_chunks = [p for p in payloads if p.get("event_type") == "tool_use"]
    assert tool_chunks, f"no tool_use chunk published; got {payloads}"
    assert tool_chunks[0]["done"] is False


async def test_handle_cmd_streaming_forwards_worker_error_from_result_event() -> None:
    """ResultLlmEvent.worker_error must propagate into the published CliChunkEvent.

    CliStreamingParser populates worker_error on cli.auth / cli.session_lost
    / cli.parse. Without this forward, the field is None on the wire and the
    hub's nats_driver synthesises `worker.internal` instead of the precise
    CLI code, breaking the P2 instrumentation chain on the streaming path.
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker
    from roxabi_contracts.errors import WorkerError

    # Arrange
    we = WorkerError(
        code="cli.session_lost",
        message="session expired — please retry",
        retryable=True,
    )
    result_event = ResultLlmEvent(
        is_error=True,
        duration_ms=42,
        session_id="sess-1",
        worker_error=we,
    )
    pool = _make_pool()
    pool.send_streaming.return_value = _make_event_iter([result_event])

    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(reply="_INBOX.reply")

    # Act
    await worker._handle_cmd(msg, _cmd_payload(stream=True))

    # Assert — published chunk preserves the structured envelope
    payloads = [
        json.loads(call.args[1].decode())
        for call in nc.publish.call_args_list
        if len(call.args) > 1
    ]
    result_chunks = [p for p in payloads if p.get("event_type") == "result"]
    assert result_chunks, f"no result chunk published; got {payloads}"
    assert result_chunks[0]["worker_error"]["code"] == "cli.session_lost"
    assert result_chunks[0]["worker_error"]["retryable"] is True


async def test_handle_cmd_validation_error_replies_worker_validation() -> None:
    """ValidationError on inbound payload publishes worker.validation envelope.

    The JSON decoded successfully (NatsAdapterBase did that) but the schema
    failed — this is the textbook `worker.validation` case, not
    `transport.parse` (which means "couldn't decode bytes/JSON").
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange — payload missing required `text` and `model_cfg` fields
    bad_payload = {
        "contract_version": "1",
        "trace_id": "trace-bad",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "pool_id": "pool-1",
        # text/model_cfg/system_prompt deliberately omitted
    }

    pool = _make_pool()
    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(reply="_INBOX.reply")

    # Act
    await worker._handle_cmd(msg, bad_payload)

    # Assert — single error chunk with worker.validation
    payloads = [
        json.loads(call.args[1].decode())
        for call in nc.publish.call_args_list
        if len(call.args) > 1
    ]
    assert len(payloads) == 1
    assert payloads[0]["is_error"] is True
    assert payloads[0]["done"] is True
    assert payloads[0]["worker_error"]["code"] == "worker.validation"
    assert payloads[0]["worker_error"]["retryable"] is False


# ---------------------------------------------------------------------------
# _handle_control — reset
# ---------------------------------------------------------------------------


async def test_handle_control_parse_failure_replies_error_ack() -> None:
    """Malformed CliControlCmd payload: worker replies ok=False (does not hang)."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc
    msg = _make_nats_msg(subject="lyra.clipool.control", reply="_INBOX.test.ctrl")
    bad_payload = {"op": "invalid_op_not_in_literal"}  # will fail validation

    # Act
    await worker._handle_control(msg, bad_payload)

    # Assert — worker replies with ok=False rather than hanging
    nc.publish.assert_awaited_once()
    call_args = nc.publish.call_args
    data = json.loads(call_args.args[1])
    assert data["ok"] is False


async def test_handle_control_dispatch_exception_replies_ok_false() -> None:
    """When pool.reset raises, _handle_control catches and replies ok=False."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    pool.reset = AsyncMock(side_effect=RuntimeError("pool reset exploded"))
    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc
    msg = _make_nats_msg(subject="lyra.clipool.control", reply="_INBOX.test.ctrl2")
    payload = _control_payload(op="reset")

    # Act
    await worker._handle_control(msg, payload)

    # Assert
    nc.publish.assert_awaited_once()
    data = json.loads(nc.publish.call_args.args[1])
    assert data["ok"] is False


async def test_handle_control_reset() -> None:
    """op='reset': pool.reset() called; CliControlAck(ok=True) published."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(subject="lyra.clipool.control", reply="_INBOX.ctrl")
    payload = _control_payload(op="reset", pool_id="pool-x")

    # Act
    await worker._handle_control(msg, payload)

    # Assert — pool.reset called with pool_id
    pool.reset.assert_awaited_once_with("pool-x")

    # Assert — reply published with ok=True
    nc.publish.assert_called_once()
    reply_body = json.loads(nc.publish.call_args.args[1].decode())
    assert reply_body["ok"] is True


# ---------------------------------------------------------------------------
# _handle_control — resume_and_reset
# ---------------------------------------------------------------------------


async def test_handle_control_resume_and_reset() -> None:
    """op='resume_and_reset': pool.resume_direct(pool_id, session_id) called."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    pool.resume_direct.return_value = True

    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(subject="lyra.clipool.control", reply="_INBOX.ctrl")
    _sid = "aaaaaaaa-bbbb-cccc-dddd-eeeeeeeeeeee"
    payload = _control_payload(op="resume_and_reset", pool_id="pool-y", session_id=_sid)

    # Act
    await worker._handle_control(msg, payload)

    # Assert — correct call signature
    pool.resume_direct.assert_awaited_once_with("pool-y", _sid)

    # Assert — reply published with ok=True (pool returned True)
    nc.publish.assert_called_once()
    reply_body = json.loads(nc.publish.call_args.args[1].decode())
    assert reply_body["ok"] is True


# ---------------------------------------------------------------------------
# _handle_control — switch_cwd
# ---------------------------------------------------------------------------


async def test_handle_control_switch_cwd(monkeypatch: pytest.MonkeyPatch) -> None:
    """op='switch_cwd': pool.switch_cwd() called with pool_id and cwd."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange — allow /tmp as the base so /tmp/workspace passes the guard
    monkeypatch.setenv("LYRA_CLAUDE_CWD", "/tmp")
    pool = _make_pool()
    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(subject="lyra.clipool.control", reply="_INBOX.ctrl")
    payload = _control_payload(op="switch_cwd", pool_id="pool-z", cwd="/tmp/workspace")

    # Act
    await worker._handle_control(msg, payload)

    # Assert — pool.switch_cwd called
    pool.switch_cwd.assert_awaited_once()
    call_args = pool.switch_cwd.call_args
    # First positional arg is pool_id; second (or kwarg cwd) is the path
    assert call_args.args[0] == "pool-z" or call_args.kwargs.get("pool_id") == "pool-z"

    # Assert — ACK published
    nc.publish.assert_called_once()
    reply_body = json.loads(nc.publish.call_args.args[1].decode())
    assert reply_body["ok"] is True


# ---------------------------------------------------------------------------
# heartbeat_payload
# ---------------------------------------------------------------------------


def test_heartbeat_payload_has_pool_count() -> None:
    """heartbeat_payload() includes pool_count == len(pool._entries)."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    pool._entries = {"a": MagicMock(), "b": MagicMock()}

    worker = CliPoolNatsWorker(pool)
    # Seed _started_at so uptime calculation does not fail
    import time

    worker._started_at = time.monotonic()

    # Act
    payload = worker.heartbeat_payload()

    # Assert
    assert payload["pool_count"] == 2


def test_heartbeat_payload_empty_pool() -> None:
    """heartbeat_payload() with empty pool gives pool_count == 0."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    pool._entries = {}

    worker = CliPoolNatsWorker(pool)
    import time

    worker._started_at = time.monotonic()

    # Act
    payload = worker.heartbeat_payload()

    # Assert
    assert payload["pool_count"] == 0


# ---------------------------------------------------------------------------
# Constructor wiring
# ---------------------------------------------------------------------------


def test_constructor_passes_correct_subject_and_queue_group() -> None:
    """Constructor sets subject='lyra.clipool.cmd' and queue_group='clipool-workers'."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange / Act
    pool = _make_pool()
    worker = CliPoolNatsWorker(pool)

    # Assert
    assert worker.subject == "lyra.clipool.cmd"
    assert worker.queue_group == "clipool-workers"


def test_constructor_custom_timeout() -> None:
    """Constructor accepts custom timeout kwarg."""
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange / Act
    pool = _make_pool()
    worker = CliPoolNatsWorker(pool, timeout=60.0)  # noqa: E501

    # Assert
    assert worker.timeout == 60.0


# ---------------------------------------------------------------------------
# Error-message sanitization (#1215, sibling of #1212)
#
# WorkerError.message is published back to the NATS bus via _make_chunk(...,
# worker_error=...) and surfaces on user-facing channels. Exception __str__
# representations from httpx / pydantic / asyncio embed file paths, byte
# sequences, and incoming field values — these must NOT leak into bus-bound
# messages. Sanitization keeps only ``type(exc).__name__`` for diagnostics.
# ---------------------------------------------------------------------------


_SENSITIVE_TOKEN = "leak42-host:4222"


async def test_classify_exception_worker_crash_does_not_leak_exception_str() -> None:
    """_classify_exception(RuntimeError(sensitive)) → worker.crash with no leak."""
    from lyra.adapters.clipool.clipool_worker import _classify_exception

    # Arrange — generic exception caught by the worker.crash branch
    leaky_exc = RuntimeError(f"unexpected failure talking to {_SENSITIVE_TOKEN}")

    # Act
    we = _classify_exception(leaky_exc)

    # Assert — code + retryable flag preserved
    assert we.code == "worker.crash"
    assert we.retryable is True

    # Assert — bus-bound message does not embed the sensitive str()
    assert _SENSITIVE_TOKEN not in we.message, (
        f"sensitive token leaked into WorkerError.message: {we.message!r}"
    )
    # Positive: class name still surfaces for diagnostic value
    assert "RuntimeError" in we.message


async def test_classify_exception_session_lost_does_not_leak_exception_str() -> None:
    """asyncio.TimeoutError(sensitive) → cli.session_lost: no leak in message."""
    import asyncio

    from lyra.adapters.clipool.clipool_worker import _classify_exception

    # Arrange — asyncio.TimeoutError can carry message content when constructed
    # with one (rare in practice, but possible from wrapping code).
    leaky_exc = asyncio.TimeoutError(f"connecting to {_SENSITIVE_TOKEN}")

    # Act
    we = _classify_exception(leaky_exc)

    # Assert — code + retryable flag preserved
    assert we.code == "cli.session_lost"
    assert we.retryable is True

    # Assert — sensitive content not on the bus, type name surfaces
    assert _SENSITIVE_TOKEN not in we.message, (
        f"sensitive token leaked into WorkerError.message: {we.message!r}"
    )
    assert "TimeoutError" in we.message


async def test_classify_exception_parse_does_not_leak_byte_sequence() -> None:
    """_classify_exception(UnicodeDecodeError) → cli.parse, no byte sequence leak.

    UnicodeDecodeError.__str__ embeds the offending byte sequence and a
    reason string — exactly the class of data this PR aims to keep off the
    bus.
    """
    from lyra.adapters.clipool.clipool_worker import _classify_exception

    # Arrange — sensitive content as the reason argument (echoed by __str__)
    leaky_exc = UnicodeDecodeError(
        "utf-8", b"\xff\xfe" + _SENSITIVE_TOKEN.encode(), 0, 1, _SENSITIVE_TOKEN
    )

    # Act
    we = _classify_exception(leaky_exc)

    # Assert — code + retryable flag preserved
    assert we.code == "cli.parse"
    assert we.retryable is False

    # Assert — sensitive content not on the bus, type name surfaces
    assert _SENSITIVE_TOKEN not in we.message, (
        f"sensitive token leaked into WorkerError.message: {we.message!r}"
    )
    assert "UnicodeDecodeError" in we.message


# ---------------------------------------------------------------------------
# Finding #9 — worker forwards agent identity fields to pool
#
# Verifies that _handle_cmd_streaming and _handle_cmd_blocking actually pass
# cmd.agent_name, cmd.agent_email, and cmd.lyra_session_id through to the pool.
# Without this test, dropping any of those fields leaves all existing tests
# green (they do not inspect the kwargs forwarded to pool.send_streaming/send).
# ---------------------------------------------------------------------------


async def test_identity_streaming_full_fields_forwarded_to_pool() -> None:
    """Streaming + full identity: send_streaming receives agent_name/email/session.

    Guards finding #9: if _handle_cmd_streaming stops forwarding any of the
    three identity kwargs, this test fails even though all other tests pass.
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    result_event = ResultLlmEvent(is_error=False, duration_ms=0)
    pool.send_streaming.return_value = _make_event_iter([result_event])

    worker = CliPoolNatsWorker(pool)
    worker._nc = AsyncMock()

    msg = _make_nats_msg(reply="_INBOX.id1")
    payload = _cmd_payload(
        stream=True,
        agent_name="agent-X",
        agent_email="x@y",
        lyra_session_id="S-1",
    )

    # Act
    await worker._handle_cmd(msg, payload)

    # Assert — all three identity kwargs forwarded to pool.send_streaming
    pool.send_streaming.assert_called_once()
    call_kwargs = pool.send_streaming.call_args.kwargs
    assert call_kwargs.get("agent_name") == "agent-X"
    assert call_kwargs.get("agent_email") == "x@y"
    assert call_kwargs.get("lyra_session_id") == "S-1"


async def test_identity_streaming_partial_identity_forwarded_to_pool() -> None:
    """Streaming + partial identity (agent_email=None): pool receives None for email.

    Guards the edge case where agent_email is absent (anonymous or trailers-only
    identity). The worker must forward the None explicitly rather than omitting
    the kwarg.
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    result_event = ResultLlmEvent(is_error=False, duration_ms=0)
    pool.send_streaming.return_value = _make_event_iter([result_event])

    worker = CliPoolNatsWorker(pool)
    worker._nc = AsyncMock()

    msg = _make_nats_msg(reply="_INBOX.id2")
    payload = _cmd_payload(
        stream=True,
        agent_name="agent-X",
        agent_email=None,
        lyra_session_id="S-1",
    )

    # Act
    await worker._handle_cmd(msg, payload)

    # Assert — agent_email=None propagated (not silently dropped)
    pool.send_streaming.assert_called_once()
    call_kwargs = pool.send_streaming.call_args.kwargs
    assert call_kwargs.get("agent_name") == "agent-X"
    assert "agent_email" in call_kwargs, (
        "agent_email kwarg absent from pool.send_streaming call"
    )
    assert call_kwargs["agent_email"] is None
    assert call_kwargs.get("lyra_session_id") == "S-1"


async def test_identity_blocking_full_fields_forwarded_to_pool() -> None:
    """Blocking + full identity: pool.send receives agent_name/email/session.

    Guards finding #9 for the non-streaming path. stream=False dispatches
    through _handle_cmd_blocking which has its own pool.send call site.
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange
    pool = _make_pool()
    pool.send.return_value = CliResult(result="ok", session_id="sid-b", error="")

    worker = CliPoolNatsWorker(pool)
    worker._nc = AsyncMock()

    msg = _make_nats_msg(reply="_INBOX.id3")
    payload = _cmd_payload(
        stream=False,
        agent_name="agent-X",
        agent_email="x@y",
        lyra_session_id="S-1",
    )

    # Act
    await worker._handle_cmd(msg, payload)

    # Assert — all three identity kwargs forwarded to pool.send
    pool.send.assert_called_once()
    call_kwargs = pool.send.call_args.kwargs
    assert call_kwargs.get("agent_name") == "agent-X"
    assert call_kwargs.get("agent_email") == "x@y"
    assert call_kwargs.get("lyra_session_id") == "S-1"


# ---------------------------------------------------------------------------
# Finding #10 — forward-compat with legacy envelope (no agent_name/agent_email)
#
# A hub running on an older contract version publishes payloads without the new
# identity fields. ContractEnvelope.extra='ignore' drops unknown fields and
# CliCmdPayload defaults those fields to None. This test verifies the *worker*
# correctly uses those defaults rather than KeyError-ing or passing unexpected
# values to the pool.
# ---------------------------------------------------------------------------


async def test_legacy_envelope_passes_none_identity_to_pool() -> None:
    """Legacy envelope (no agent_name/agent_email) → pool.send_streaming gets None/None.

    Guards finding #10: the legacy dict is constructed field-by-field (not via
    model_dump(exclude=...)) so the test accurately simulates a payload that
    never carried these fields at the serialization boundary.

    The critical mechanism under test: ContractEnvelope.extra='ignore' + field
    defaults let CliCmdPayload.model_validate succeed and produce None for both
    agent_name and agent_email; the worker then forwards those Nones to the pool.
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange — legacy-shape payload constructed from scratch (no new fields present)
    legacy_payload: dict = {
        "contract_version": "1",
        "trace_id": "trace-legacy",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "pool_id": "pool-legacy",
        "lyra_session_id": "S-legacy",
        "text": "what time is it",
        "model_cfg": {},
        "system_prompt": "",
        "stream": True,
        # agent_name and agent_email intentionally absent — old hub schema
    }

    pool = _make_pool()
    result_event = ResultLlmEvent(is_error=False, duration_ms=0)
    pool.send_streaming.return_value = _make_event_iter([result_event])

    worker = CliPoolNatsWorker(pool)
    worker._nc = AsyncMock()

    msg = _make_nats_msg(reply="_INBOX.legacy")

    # Act
    await worker._handle_cmd(msg, legacy_payload)

    # Assert — pool.send_streaming called with both identity fields as None
    pool.send_streaming.assert_called_once()
    call_kwargs = pool.send_streaming.call_args.kwargs
    assert "agent_name" in call_kwargs, (
        "agent_name kwarg absent; worker must forward the field even when None"
    )
    assert "agent_email" in call_kwargs, (
        "agent_email kwarg absent; worker must forward the field even when None"
    )
    assert call_kwargs["agent_name"] is None
    assert call_kwargs["agent_email"] is None
    assert call_kwargs.get("lyra_session_id") == "S-legacy"


async def test_handle_cmd_validation_error_does_not_leak_payload_fields() -> None:
    """_handle_cmd: ValidationError.message must not embed incoming payload values.

    Activates the leak channel by sending a sensitive value through a typed
    field (``stream`` requires bool) — Pydantic echoes the bad input verbatim
    in the validation error. The token is short enough to fit inside
    Pydantic's ~50-char ``input_value`` truncation window, so the negative
    assertion is meaningful (i.e. the test fails if sanitization is reverted).
    """
    from lyra.adapters.clipool.clipool_worker import CliPoolNatsWorker

    # Arrange — sensitive value in a typed field that Pydantic echoes
    bad_payload = {
        "contract_version": "1",
        "trace_id": "trace-leak",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "pool_id": "pool-1",
        "lyra_session_id": "sess-1",
        "text": "hello",
        "model_cfg": {},
        "system_prompt": "",
        "stream": f"not-a-bool-{_SENSITIVE_TOKEN}",  # bool_parsing → echoes value
    }

    pool = _make_pool()
    worker = CliPoolNatsWorker(pool)
    nc = AsyncMock()
    worker._nc = nc

    msg = _make_nats_msg(reply="_INBOX.reply.leak")

    # Act
    await worker._handle_cmd(msg, bad_payload)

    # Assert — single error chunk published
    nc.publish.assert_called_once()
    data = json.loads(nc.publish.call_args.args[1].decode())
    assert data["worker_error"]["code"] == "worker.validation"

    # Assert — sensitive payload value did not leak into bus-bound message
    message = data["worker_error"]["message"]
    assert _SENSITIVE_TOKEN not in message, (
        f"sensitive payload field leaked into worker_error.message: {message!r}"
    )
    # Positive: literal fallback preserved (guards against regressions to
    # empty/None messages that would still pass the negative assertion).
    assert message == "CliCmdPayload validation failed"
