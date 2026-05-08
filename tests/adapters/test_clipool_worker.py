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
