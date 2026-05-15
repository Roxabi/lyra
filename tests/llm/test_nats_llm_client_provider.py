"""RED tests for NatsLlmClient LlmProvider conformance — issue #1119.

These tests define the TARGET API that T2-T9 will implement. They are
intentionally RED today: NatsLlmClient does not yet expose complete(),
is_alive(), or the conformant stream() / capabilities class attribute.

Run selectors:
  -k protocol   → PART A (TestProtocolShape)
  -k error      → PART B (TestErrorMappingComplete, TestErrorMappingStream)
"""

from __future__ import annotations

import inspect
import json
import time
from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent
from lyra.core.ports.llm import LlmProvider, LlmResult
from lyra.nats.nats_llm_client import NatsLlmClient
from roxabi_contracts.errors import KNOWN_CODES
from roxabi_contracts.llm.models import LlmRequest

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_CONTRACT_VERSION = "1"


def _make_nc() -> AsyncMock:
    """Return a fresh AsyncMock NATS client."""
    nc = AsyncMock()
    nc.new_inbox = MagicMock(return_value="_INBOX.test123")
    return nc


def _seed_registry(client: NatsLlmClient, worker_id: str = "w-1") -> None:
    """Register a live worker in the client registry."""
    client._registry.record_heartbeat(
        {
            "worker_id": worker_id,
            "active_requests": 0,
        }
    )


def _make_model_cfg() -> ModelConfig:
    """Return a minimal ModelConfig for test use."""
    return ModelConfig(model="gpt-4o", backend="litellm")


def _ok_response_bytes(request_id: str = "req0001", text: str = "hi") -> bytes:
    """Return a valid LlmResponse(ok=True) as JSON bytes."""
    payload = {
        "contract_version": _CONTRACT_VERSION,
        "trace_id": "tst-trace",
        "issued_at": "2026-01-01T00:00:00+00:00",
        "ok": True,
        "request_id": request_id,
        "text": text,
    }
    return json.dumps(payload).encode()


def _chunk_bytes(  # noqa: PLR0913
    *,
    request_id: str = "req0001",
    delta: str | None = None,
    done: bool = False,
    is_error: bool = False,
    error: str | None = None,
    duration_ms: int | None = None,
    worker_error: dict | None = None,
) -> bytes:
    """Return a valid LlmChunkEvent JSON payload."""
    payload: dict = {
        "contract_version": _CONTRACT_VERSION,
        "trace_id": "tst-trace",
        "issued_at": "2026-01-01T00:00:00+00:00",
        "request_id": request_id,
        "done": done,
        "is_error": is_error,
    }
    if delta is not None:
        payload["delta"] = delta
    if error is not None:
        payload["error"] = error
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    if worker_error is not None:
        payload["worker_error"] = worker_error
    return json.dumps(payload).encode()


def _fake_reply(data: bytes) -> MagicMock:
    msg = MagicMock()
    msg.data = data
    return msg


def _open_circuit(client: NatsLlmClient) -> None:
    """Force circuit breaker open without going through real failure count."""
    client._cb._open_until = time.monotonic() + 100.0


# ---------------------------------------------------------------------------
# PART A — Protocol shape (T1, SC-1..SC-5)
# ---------------------------------------------------------------------------


class TestProtocolShape:
    """Verify NatsLlmClient satisfies the LlmProvider protocol contract."""

    def test_capabilities_class_attr(self) -> None:
        # Arrange / Act / Assert
        assert NatsLlmClient.capabilities == {"streaming": True, "auth": "nats"}  # type: ignore[attr-defined]

    @pytest.mark.asyncio
    async def test_isinstance_llm_provider(self) -> None:
        """Verify the client implements LlmProvider behaviorally, not just structurally.

        runtime_checkable isinstance checks attribute names only — these assertions
        exercise the actual contract: signature parity + return-type fidelity.
        """
        mock_nc = _make_nc()
        client = NatsLlmClient(nc=mock_nc)
        # Behavioral checks — runtime_checkable isinstance is name-only.
        # These exercise the actual contract: signature parity + return-type fidelity.
        assert isinstance(client, LlmProvider)
        assert client.capabilities == {"streaming": True, "auth": "nats"}
        # is_alive must accept str and return bool; default unconnected nc → False
        mock_nc.is_connected = False
        assert client.is_alive("any-pool") is False
        # complete() must be an awaitable coroutine, not a sync attribute
        import inspect as _inspect

        assert _inspect.iscoroutinefunction(client.complete)
        # stream() must be an async function returning AsyncIterator
        assert _inspect.iscoroutinefunction(client.stream)

    def test_has_complete_method_correct_signature(self) -> None:
        # Arrange
        nc = _make_nc()
        client = NatsLlmClient(nc)
        # Act
        sig = inspect.signature(client.complete)  # type: ignore[attr-defined]
        params = list(sig.parameters.keys())
        # Assert — positional: pool_id, text, model_cfg, system_prompt
        # kw-only: messages
        assert params == ["pool_id", "text", "model_cfg", "system_prompt", "messages"]
        assert sig.parameters["messages"].kind == inspect.Parameter.KEYWORD_ONLY

    def test_has_is_alive_method(self) -> None:
        # Arrange
        nc = _make_nc()
        client = NatsLlmClient(nc)
        # Act
        sig = inspect.signature(client.is_alive)  # type: ignore[attr-defined]
        params = list(sig.parameters.keys())
        # Assert
        assert params == ["pool_id"]

    def test_has_stream_method_correct_signature(self) -> None:
        # Arrange
        nc = _make_nc()
        client = NatsLlmClient(nc)
        # Act
        sig = inspect.signature(client.stream)
        params = list(sig.parameters.keys())
        # Assert — positional: pool_id, text, model_cfg, system_prompt
        # kw-only: messages
        assert params == ["pool_id", "text", "model_cfg", "system_prompt", "messages"]
        assert sig.parameters["messages"].kind == inspect.Parameter.KEYWORD_ONLY

    @pytest.mark.asyncio
    async def test_is_alive_returns_false_when_nc_disconnected(self) -> None:
        """is_alive returns False when nc.is_connected is False.

        The registry may have a live worker — nc disconnect takes priority.
        """
        mock_nc = _make_nc()
        mock_nc.is_connected = False
        client = NatsLlmClient(nc=mock_nc)
        _seed_registry(client, worker_id="w-1")  # registry has live worker
        assert client.is_alive("p-1") is False

    @pytest.mark.asyncio
    async def test_is_alive_returns_false_when_no_live_workers(self) -> None:
        """is_alive returns False when the registry has no live workers.

        nc.is_connected is True — the empty registry is the deciding factor.
        """
        mock_nc = _make_nc()
        mock_nc.is_connected = True
        client = NatsLlmClient(nc=mock_nc)
        # registry is empty by default — no _seed_registry call
        assert client.is_alive("p-1") is False

    @pytest.mark.asyncio
    async def test_complete_happy_path(self) -> None:
        # Arrange
        nc = _make_nc()
        fake_reply = _fake_reply(_ok_response_bytes(text="hi"))
        nc.request = AsyncMock(return_value=fake_reply)
        client = NatsLlmClient(nc)
        _seed_registry(client)
        mc = _make_model_cfg()

        # Act
        result = await client.complete(  # type: ignore[attr-defined]
            "pool-1", "hello", mc, "you are helpful"
        )

        # Assert — result shape
        assert isinstance(result, LlmResult)
        assert result.ok is True
        assert result.result == "hi"

        # Assert — session_id matches outbound LlmRequest.trace_id
        call_args = nc.request.call_args
        # call_args[0] = (subject, payload, ...) or via kwargs
        raw_payload = call_args[0][1] if call_args[0] else call_args[1]["payload"]
        outbound = LlmRequest.model_validate_json(raw_payload)
        assert result.session_id == outbound.trace_id

    @pytest.mark.asyncio
    async def test_complete_text_folding_passthrough_last_matches(  # noqa: E501
        self,
    ) -> None:
        """Text-folding branch 2: last.content == text → pass messages unchanged.

        No duplication: the user message is NOT appended a second time.
        """
        nc = _make_nc()
        fake_reply = _fake_reply(_ok_response_bytes(text="ok"))
        nc.request = AsyncMock(return_value=fake_reply)
        client = NatsLlmClient(nc)
        _seed_registry(client)
        mc = _make_model_cfg()
        messages = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "reply"},
            {"role": "user", "content": "hello"},  # last.content == text
        ]

        result = await client.complete(  # type: ignore[attr-defined]
            "pool", "hello", mc, "sys", messages=messages
        )

        assert result.ok
        # Inspect outbound payload — messages should be passed verbatim (no append)
        call_args = nc.request.call_args
        raw_payload = call_args[0][1] if call_args[0] else call_args[1]["payload"]
        outbound = LlmRequest.model_validate_json(raw_payload)
        assert outbound.messages == messages  # exact pass-through, no duplication
        assert len(outbound.messages) == 3

    @pytest.mark.asyncio
    async def test_stream_yields_text_then_result(self) -> None:
        # Arrange
        nc = _make_nc()
        client = NatsLlmClient(nc)
        _seed_registry(client)
        mc = _make_model_cfg()

        # Two chunks: delta="ab" done=False, then done=True with duration_ms=42
        chunk1 = _fake_reply(_chunk_bytes(delta="ab", done=False, request_id="req0001"))
        chunk2 = _fake_reply(
            _chunk_bytes(done=True, duration_ms=42, request_id="req0001")
        )

        mock_sub = AsyncMock()
        mock_sub.next_msg = AsyncMock(side_effect=[chunk1, chunk2])
        nc.subscribe = AsyncMock(return_value=mock_sub)
        nc.publish = AsyncMock()

        # Act — calls target the post-T5 signature; ignore removed when T5 lands
        events = []
        stream = client.stream("pool-1", "hello", mc, "sys")  # type: ignore[call-arg]
        async for evt in await stream:
            events.append(evt)

        # Assert
        assert len(events) == 2
        assert events[0] == TextLlmEvent(text="ab")
        assert isinstance(events[1], ResultLlmEvent)
        assert events[1].is_error is False
        assert events[1].duration_ms == 42
        assert events[1].cost_usd is None

    @pytest.mark.asyncio
    async def test_stream_yields_terminal_result_on_error(self) -> None:
        # Arrange
        nc = _make_nc()
        client = NatsLlmClient(nc)
        _seed_registry(client)
        mc = _make_model_cfg()

        # Error terminal chunk
        chunk_error = _fake_reply(
            _chunk_bytes(is_error=True, error="boom", done=True, request_id="req0001")
        )

        mock_sub = AsyncMock()
        mock_sub.next_msg = AsyncMock(side_effect=[chunk_error])
        nc.subscribe = AsyncMock(return_value=mock_sub)
        nc.publish = AsyncMock()

        # Act — calls target the post-T5 signature; ignore removed when T5 lands
        events = []
        stream = client.stream("pool-1", "hello", mc, "sys")  # type: ignore[call-arg]
        async for evt in await stream:
            events.append(evt)

        # Assert — final event is ResultLlmEvent with is_error=True
        assert len(events) >= 1
        last = events[-1]
        assert isinstance(last, ResultLlmEvent)
        assert last.is_error is True
        assert last.error_text == "boom"


# ---------------------------------------------------------------------------
# PART B — Error mapping (T7, SC-7)
# ---------------------------------------------------------------------------
# 8 rows from spec §"Error-code mapping":
#   transport_timeout, transport_no_responders, transport_parse,
#   transport_contract_mismatch, worker_capacity, transport_error_oversize,
#   worker_error_propagated, worker_internal_legacy
# ---------------------------------------------------------------------------

_ERROR_PARAMS = [
    # (trigger_id, nc_kwargs, expected_code, expected_retryable)
    pytest.param(
        "timeout",
        {"side_effect": TimeoutError()},
        "transport.timeout",
        True,
        id="transport_timeout",
    ),
    pytest.param(
        "no_responders",
        {"side_effect": nats.errors.NoRespondersError()},
        "transport.no_responders",
        True,
        id="transport_no_responders",
    ),
    pytest.param(
        "parse",
        {"return_value": _fake_reply(b"not-json{{{")},
        "transport.parse",
        False,
        id="transport_parse",
    ),
    # contract_mismatch — T8 must implement the contract-version check;
    # today NatsLlmClient doesn't inspect contract_version on the reply.
    # When removing this xfail after T8 implements CONTRACT_VERSION checking,
    # also add a positive-direction test asserting that a response with matching
    # contract_version is accepted (the "guard exists" direction). Without it,
    # a subsequent deletion of the version check would silently pass.
    pytest.param(
        "contract_mismatch",
        {
            "return_value": _fake_reply(
                json.dumps(
                    {
                        "contract_version": "99",  # wrong version
                        "trace_id": "t",
                        "issued_at": "2026-01-01T00:00:00+00:00",
                        "ok": True,
                        "request_id": "req0001",
                        "text": "hi",
                    }
                ).encode()
            )
        },
        "transport.contract_mismatch",
        False,
        marks=pytest.mark.xfail(
            reason="T8 must implement contract-version check; "
            "current code silently accepts any contract_version",
            strict=True,
        ),
        id="transport_contract_mismatch",
    ),
    pytest.param(
        "capacity",
        None,  # special: open CB before call
        "worker.capacity",
        True,
        id="worker_capacity",
    ),
    pytest.param(
        "oversize",
        {"side_effect": nats.errors.Error("max_payload exceeded (1048576 bytes)")},
        "transport.error",
        False,
        id="transport_error_oversize",
    ),
    pytest.param(
        "generic_nats_error",
        {"side_effect": nats.errors.Error("connection reset by peer")},
        "transport.error",
        True,  # retryable=True for generic nats errors (not oversize)
        id="transport_error_generic",
    ),
    pytest.param(
        "worker_propagated",
        {
            "return_value": _fake_reply(
                json.dumps(
                    {
                        "contract_version": _CONTRACT_VERSION,
                        "trace_id": "t",
                        "issued_at": "2026-01-01T00:00:00+00:00",
                        "ok": False,
                        "request_id": "req0001",
                        "error": "quota exceeded",
                        "worker_error": {
                            "code": "llm.rate_limit",
                            "message": "quota",
                            "retryable": True,
                        },
                    }
                ).encode()
            )
        },
        "llm.rate_limit",
        True,
        id="worker_error_propagated",
    ),
    pytest.param(
        "worker_internal_legacy",
        {
            "return_value": _fake_reply(
                json.dumps(
                    {
                        "contract_version": _CONTRACT_VERSION,
                        "trace_id": "t",
                        "issued_at": "2026-01-01T00:00:00+00:00",
                        "ok": False,
                        "request_id": "req0001",
                        "error": "oops",
                    }
                ).encode()
            )
        },
        "worker.internal",
        True,
        id="worker_internal_legacy",
    ),
]


class TestErrorMappingComplete:
    """complete() must map every error scenario to LlmResult.worker_error, not raise."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "trigger_id,nc_kwargs,expected_code,expected_retryable",
        _ERROR_PARAMS,
    )
    async def test_error_mapping(
        self,
        trigger_id: str,
        nc_kwargs: dict | None,
        expected_code: str,
        expected_retryable: bool,
    ) -> None:
        # Arrange
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()

        if trigger_id == "capacity":
            # worker_capacity: open the circuit breaker before seeding
            _seed_registry(client)
            _open_circuit(client)
        else:
            _seed_registry(client)
            nc.request = AsyncMock(**(nc_kwargs or {}))

        # Act — complete() targets post-T2 API; ignore removed when T2 lands
        result = await client.complete(  # type: ignore[attr-defined]
            "pool-1", "hi", mc, "sys"
        )

        # Assert
        assert result.worker_error is not None, (
            f"Expected worker_error for {expected_code!r} but got None "
            f"(result.error={result.error!r})"
        )
        assert result.worker_error.code == expected_code, (
            f"Expected code {expected_code!r}, got {result.worker_error.code!r}"
        )
        assert result.worker_error.retryable == expected_retryable, (
            f"Expected retryable={expected_retryable} for {expected_code!r}, "
            f"got {result.worker_error.retryable}"
        )
        assert result.worker_error.code in KNOWN_CODES, (
            f"Code {result.worker_error.code!r} not registered in KNOWN_CODES"
        )


# ---------------------------------------------------------------------------
# Stream-path error mapping — mirrors TestErrorMappingComplete for streaming
# ---------------------------------------------------------------------------

_STREAM_ERROR_PARAMS = [
    # (trigger_id, trigger_type, trigger_data, expected_code, expected_retryable)
    pytest.param(
        "timeout",
        "next_msg_timeout",
        None,
        "transport.timeout",
        True,
        id="transport_timeout",
    ),
    pytest.param(
        "no_responders",
        "publish_no_responders",
        None,
        "transport.no_responders",
        True,
        id="transport_no_responders",
    ),
    pytest.param(
        "parse",
        "malformed_chunk",
        b"not-json{{{",
        "transport.parse",
        False,
        id="transport_parse",
    ),
    # contract_mismatch on stream: chunk carries wrong contract_version
    # When removing this xfail after T8 implements CONTRACT_VERSION checking,
    # also add a positive-direction test asserting that a response with matching
    # contract_version is accepted (the "guard exists" direction). Without it,
    # a subsequent deletion of the version check would silently pass.
    pytest.param(
        "contract_mismatch",
        "malformed_chunk",
        json.dumps(
            {
                "contract_version": "99",
                "trace_id": "t",
                "issued_at": "2026-01-01T00:00:00+00:00",
                "request_id": "req0001",
                "done": True,
                "is_error": False,
            }
        ).encode(),
        "transport.contract_mismatch",
        False,
        marks=pytest.mark.xfail(
            reason="T8 must implement contract-version check on stream chunks; "
            "current code silently accepts any contract_version",
            strict=True,
        ),
        id="transport_contract_mismatch",
    ),
    pytest.param(
        "capacity",
        "open_circuit",
        None,
        "worker.capacity",
        True,
        id="worker_capacity",
    ),
    pytest.param(
        "oversize",
        "publish_oversize",
        None,
        "transport.error",
        False,
        id="transport_error_oversize",
    ),
    pytest.param(
        "generic_nats_error",
        "publish_generic_nats_error",
        None,
        "transport.error",
        True,  # retryable=True for generic nats errors (not oversize)
        id="transport_error_generic",
    ),
    pytest.param(
        "worker_propagated",
        "error_chunk",
        {
            "is_error": True,
            "error": "quota exceeded",
            "done": True,
            "worker_error": {
                "code": "llm.rate_limit",
                "message": "quota",
                "retryable": True,
            },
        },
        "llm.rate_limit",
        True,
        id="worker_error_propagated",
    ),
    pytest.param(
        "worker_internal_legacy",
        "error_chunk",
        {
            "is_error": True,
            "error": "oops",
            "done": True,
        },
        "worker.internal",
        True,
        id="worker_internal_legacy",
    ),
]


class TestErrorMappingStream:
    """stream() yields terminal ResultLlmEvent with worker_error on every error.

    All client.stream() calls here target the post-T5 signature:
      stream(pool_id, text, model_cfg, system_prompt, *, messages)
    The `# type: ignore[call-arg]` suppresses pyright arity errors against the
    current (pre-T5) signature. Remove ignores once T5 lands.
    """

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "trigger_id,trigger_type,trigger_data,expected_code,expected_retryable",
        _STREAM_ERROR_PARAMS,
    )
    async def test_error_mapping(  # noqa: C901, PLR0912, PLR0915
        self,
        trigger_id: str,  # noqa: ARG002 — label only, used by parametrize ids
        trigger_type: str,
        trigger_data: object,
        expected_code: str,
        expected_retryable: bool,
    ) -> None:
        # Arrange
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        events: list[object] = []

        if trigger_type == "open_circuit":
            # worker_capacity: open the circuit breaker before seeding
            _seed_registry(client)
            _open_circuit(client)
            stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
            async for evt in await stream:
                events.append(evt)

        elif trigger_type == "publish_no_responders":
            _seed_registry(client)
            mock_sub = AsyncMock()
            nc.subscribe = AsyncMock(return_value=mock_sub)
            nc.publish = AsyncMock(side_effect=nats.errors.NoRespondersError())
            stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
            async for evt in await stream:
                events.append(evt)

        elif trigger_type == "publish_oversize":
            _seed_registry(client)
            mock_sub = AsyncMock()
            nc.subscribe = AsyncMock(return_value=mock_sub)
            nc.publish = AsyncMock(
                side_effect=nats.errors.Error("max_payload exceeded (1048576 bytes)")
            )
            stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
            async for evt in await stream:
                events.append(evt)

        elif trigger_type == "publish_generic_nats_error":
            _seed_registry(client)
            mock_sub = AsyncMock()
            nc.subscribe = AsyncMock(return_value=mock_sub)
            nc.publish = AsyncMock(
                side_effect=nats.errors.Error("connection reset by peer")
            )
            stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
            async for evt in await stream:
                events.append(evt)

        elif trigger_type == "next_msg_timeout":
            _seed_registry(client)
            mock_sub = AsyncMock()
            mock_sub.next_msg = AsyncMock(side_effect=TimeoutError())
            nc.subscribe = AsyncMock(return_value=mock_sub)
            nc.publish = AsyncMock()
            stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
            async for evt in await stream:
                events.append(evt)

        elif trigger_type == "malformed_chunk":
            _seed_registry(client)
            bad_msg = _fake_reply(trigger_data)  # type: ignore[arg-type]
            mock_sub = AsyncMock()
            mock_sub.next_msg = AsyncMock(side_effect=[bad_msg])
            nc.subscribe = AsyncMock(return_value=mock_sub)
            nc.publish = AsyncMock()
            stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
            async for evt in await stream:
                events.append(evt)

        elif trigger_type == "error_chunk":
            _seed_registry(client)
            assert isinstance(trigger_data, dict)
            chunk_data = _chunk_bytes(
                request_id="req0001",
                is_error=trigger_data.get("is_error", False),
                error=trigger_data.get("error"),
                done=trigger_data.get("done", False),
                worker_error=trigger_data.get("worker_error"),
            )
            error_msg = _fake_reply(chunk_data)
            mock_sub = AsyncMock()
            mock_sub.next_msg = AsyncMock(side_effect=[error_msg])
            nc.subscribe = AsyncMock(return_value=mock_sub)
            nc.publish = AsyncMock()
            stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
            async for evt in await stream:
                events.append(evt)

        else:
            pytest.fail(f"Unknown trigger_type: {trigger_type!r}")

        # Assert — last event is ResultLlmEvent with is_error=True + worker_error
        assert len(events) >= 1, "Expected at least one event from stream()"
        last = events[-1]
        assert isinstance(last, ResultLlmEvent), (
            f"Expected final event to be ResultLlmEvent, got {type(last).__name__}"
        )
        assert last.is_error is True, (
            f"Expected final ResultLlmEvent.is_error=True for {expected_code!r}"
        )
        assert last.worker_error is not None, (
            f"Expected worker_error for {expected_code!r} but got None"
        )
        assert last.worker_error.code == expected_code, (
            f"Expected code {expected_code!r}, got {last.worker_error.code!r}"
        )
        assert last.worker_error.retryable == expected_retryable, (
            f"Expected retryable={expected_retryable} for {expected_code!r}, "
            f"got {last.worker_error.retryable}"
        )
        assert last.worker_error.code in KNOWN_CODES, (
            f"Code {last.worker_error.code!r} not registered in KNOWN_CODES"
        )


# ---------------------------------------------------------------------------
# PART D — Bus-boundary info-leak guards (#1212)
# ---------------------------------------------------------------------------
# Verifies that sensitive substrings from exception ``__str__`` (hostnames,
# subject names, connection strings, file paths) do NOT leak into the
# bus-visible fields: ``ResultLlmEvent.error_text`` and
# ``WorkerError.message``. Sanitization uses ``type(exc).__name__`` only.
# ---------------------------------------------------------------------------


_SENSITIVE_HOST = "secret.internal.example:4222"


class TestErrorTextSanitization:
    """Bus-bound error fields must not embed exception __str__ values."""

    @pytest.mark.asyncio
    async def test_stream_no_responders_does_not_leak_exception_str(self) -> None:
        """publish → NoRespondersError with sensitive str() → no leak in error_text."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        leaky_exc = nats.errors.NoRespondersError(
            f"no responders on subject lyra.{_SENSITIVE_HOST}"
        )
        nc.subscribe = AsyncMock(return_value=AsyncMock())
        nc.publish = AsyncMock(side_effect=leaky_exc)

        events: list[object] = []
        stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        async for evt in await stream:
            events.append(evt)

        last = events[-1]
        assert isinstance(last, ResultLlmEvent)
        assert last.error_text is not None
        assert _SENSITIVE_HOST not in last.error_text, (
            f"sensitive host leaked into error_text: {last.error_text!r}"
        )
        assert last.worker_error is not None
        assert _SENSITIVE_HOST not in last.worker_error.message, (
            f"sensitive host leaked into worker_error.message: "
            f"{last.worker_error.message!r}"
        )
        # Positive: class name still surfaces for diagnostic value.
        assert "NoRespondersError" in last.error_text

    @pytest.mark.asyncio
    async def test_stream_timeout_does_not_leak_exception_str(self) -> None:
        """next_msg → TimeoutError with sensitive str() → no leak in error_text."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        leaky_exc = TimeoutError(
            f"deadline exceeded waiting for {_SENSITIVE_HOST}"
        )
        mock_sub = AsyncMock()
        mock_sub.next_msg = AsyncMock(side_effect=leaky_exc)
        nc.subscribe = AsyncMock(return_value=mock_sub)
        nc.publish = AsyncMock()

        events: list[object] = []
        stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        async for evt in await stream:
            events.append(evt)

        last = events[-1]
        assert isinstance(last, ResultLlmEvent)
        assert last.error_text is not None
        assert _SENSITIVE_HOST not in last.error_text
        assert last.worker_error is not None
        assert _SENSITIVE_HOST not in last.worker_error.message
        assert "TimeoutError" in last.error_text

    @pytest.mark.asyncio
    async def test_stream_transport_error_does_not_leak_exception_str(
        self,
    ) -> None:
        """publish → generic NATS error with sensitive str() → no leak."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        leaky_exc = nats.errors.Error(
            f"connection reset by peer host={_SENSITIVE_HOST}"
        )
        nc.subscribe = AsyncMock(return_value=AsyncMock())
        nc.publish = AsyncMock(side_effect=leaky_exc)

        events: list[object] = []
        stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        async for evt in await stream:
            events.append(evt)

        last = events[-1]
        assert isinstance(last, ResultLlmEvent)
        assert last.error_text is not None
        assert _SENSITIVE_HOST not in last.error_text
        assert last.worker_error is not None
        assert _SENSITIVE_HOST not in last.worker_error.message

    @pytest.mark.asyncio
    async def test_stream_malformed_chunk_does_not_leak_exception_str(
        self,
    ) -> None:
        """model_validate_json → ValidationError with sensitive str() → no leak."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        # Pydantic ValidationError messages embed field values from the
        # malformed payload; this fixture injects a sensitive value.
        bad_payload = json.dumps(
            {"contract_version": "1", "request_id": _SENSITIVE_HOST}
        ).encode()
        bad_msg = _fake_reply(bad_payload)
        mock_sub = AsyncMock()
        mock_sub.next_msg = AsyncMock(side_effect=[bad_msg])
        nc.subscribe = AsyncMock(return_value=mock_sub)
        nc.publish = AsyncMock()

        events: list[object] = []
        stream = client.stream("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        async for evt in await stream:
            events.append(evt)

        last = events[-1]
        assert isinstance(last, ResultLlmEvent)
        assert last.error_text is not None
        assert _SENSITIVE_HOST not in last.error_text
        assert last.worker_error is not None
        assert _SENSITIVE_HOST not in last.worker_error.message

    @pytest.mark.asyncio
    async def test_complete_no_responders_does_not_leak_exception_str(self) -> None:
        """Non-streaming complete() path also sanitizes (#1212 scope expansion)."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        leaky_exc = nats.errors.NoRespondersError(
            f"no responders host={_SENSITIVE_HOST}"
        )
        nc.request = AsyncMock(side_effect=leaky_exc)

        result = await client.complete("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        assert result.error is not None
        assert _SENSITIVE_HOST not in result.error
        assert "NoRespondersError" in result.error
        assert result.worker_error is not None
        assert _SENSITIVE_HOST not in result.worker_error.message

    @pytest.mark.asyncio
    async def test_complete_timeout_does_not_leak_exception_str(self) -> None:
        """complete() TimeoutError path sanitizes (coverage symmetry w/ stream)."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        leaky_exc = TimeoutError(f"deadline exceeded for {_SENSITIVE_HOST}")
        nc.request = AsyncMock(side_effect=leaky_exc)

        result = await client.complete("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        assert result.error is not None
        assert _SENSITIVE_HOST not in result.error
        assert result.worker_error is not None
        assert _SENSITIVE_HOST not in result.worker_error.message

    @pytest.mark.asyncio
    async def test_complete_transport_error_does_not_leak_exception_str(
        self,
    ) -> None:
        """complete() generic nats.errors.Error path sanitizes."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        leaky_exc = nats.errors.Error(
            f"connection reset by peer host={_SENSITIVE_HOST}"
        )
        nc.request = AsyncMock(side_effect=leaky_exc)

        result = await client.complete("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        assert result.error is not None
        assert _SENSITIVE_HOST not in result.error
        assert "Error" in result.error
        assert result.worker_error is not None
        assert _SENSITIVE_HOST not in result.worker_error.message

    @pytest.mark.asyncio
    async def test_complete_invalid_response_does_not_leak_exception_str(
        self,
    ) -> None:
        """complete() ValidationError on reply parse sanitizes."""
        nc = _make_nc()
        client = NatsLlmClient(nc)
        mc = _make_model_cfg()
        _seed_registry(client)

        # Inject sensitive value as a payload field that Pydantic
        # ValidationError will embed in its str() representation.
        bad_payload = json.dumps(
            {"contract_version": "1", "request_id": _SENSITIVE_HOST}
        ).encode()
        bad_reply = _fake_reply(bad_payload)
        nc.request = AsyncMock(return_value=bad_reply)

        result = await client.complete("pool-1", "hi", mc, "sys")  # type: ignore[call-arg]
        assert result.error is not None
        assert _SENSITIVE_HOST not in result.error
        assert result.worker_error is not None
        assert _SENSITIVE_HOST not in result.worker_error.message
