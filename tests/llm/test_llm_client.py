"""LlmClient layer tests — orchestration of codec + pool.

Spec § Slice S4. Mocks WorkerPoolClient; codec is real (LlmCodec is pure).
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent
from lyra.core.ports.llm import LlmResult
from lyra.llm.llm_client import LlmClient
from lyra.llm.llm_codec import LlmCodec
from lyra.transport._result import Err, Ok, SanitizedError
from lyra.transport.worker_pool_client import WorkerPoolClient

CONTRACT_VERSION = "1"

_MODEL = ModelConfig(backend="nats", model="claude-sonnet-4-6")


def _make_pool() -> MagicMock:
    pool = MagicMock(spec=WorkerPoolClient)
    pool.is_pool_alive = MagicMock(return_value=True)
    pool.stop = AsyncMock()
    return pool


def _make_client(pool: MagicMock) -> LlmClient:
    return LlmClient(pool, LlmCodec())


def _ok_response_bytes(*, text: str = "world") -> bytes:
    return json.dumps(
        {
            "contract_version": CONTRACT_VERSION,
            "trace_id": "t1",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "ok": True,
            "request_id": "r1",
            "text": text,
        }
    ).encode()


def _chunk_bytes(
    *,
    delta: str | None,
    done: bool,
    is_error: bool = False,
    duration_ms: int | None = None,
) -> bytes:
    payload: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "trace_id": "t1",
        "issued_at": datetime.now(timezone.utc).isoformat(),
        "request_id": "req1",
        "delta": delta,
        "done": done,
        "is_error": is_error,
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    return json.dumps(payload).encode()


class TestLlmClientIsAlive:
    def test_is_alive_delegates_to_pool_alive(self) -> None:
        pool = _make_pool()
        pool.is_pool_alive.return_value = True
        client = _make_client(pool)
        assert client.is_alive("any-pool-id") is True
        pool.is_pool_alive.assert_called_once()

    def test_is_alive_delegates_to_pool_dead(self) -> None:
        pool = _make_pool()
        pool.is_pool_alive.return_value = False
        client = _make_client(pool)
        assert client.is_alive("any-pool-id") is False


class TestLlmClientComplete:
    @pytest.mark.asyncio
    async def test_complete_returns_llm_result_on_success(self) -> None:
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(
            return_value=Ok(_ok_response_bytes(text="hello"))
        )
        client = _make_client(pool)
        result = await client.complete("p1", "hello", _MODEL, "sys")
        assert isinstance(result, LlmResult)
        assert result.error == ""
        assert result.result == "hello"

    @pytest.mark.asyncio
    async def test_complete_delegates_to_pool_request_with_routing(self) -> None:
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(_ok_response_bytes()))
        client = _make_client(pool)
        await client.complete("p1", "hello", _MODEL, "sys")
        pool.request_with_routing.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_complete_passes_max_attempts_one(self) -> None:
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(_ok_response_bytes()))
        client = _make_client(pool)
        await client.complete("p1", "hello", _MODEL, "sys")
        _, kwargs = pool.request_with_routing.call_args
        assert kwargs.get("max_attempts") == 1

    @pytest.mark.asyncio
    async def test_complete_maps_transport_err_to_llm_result_error(self) -> None:
        pool = _make_pool()
        err = SanitizedError(
            code="transport.timeout", message="TimeoutError", retryable=True
        )
        pool.request_with_routing = AsyncMock(return_value=Err(err))
        client = _make_client(pool)
        result = await client.complete("p1", "hello", _MODEL, "sys")
        assert isinstance(result, LlmResult)
        assert result.error == "TimeoutError"
        assert result.retryable is True

    @pytest.mark.asyncio
    async def test_complete_passes_messages_to_codec(self) -> None:
        pool = _make_pool()
        pool.request_with_routing = AsyncMock(return_value=Ok(_ok_response_bytes()))
        client = _make_client(pool)
        messages = [{"role": "user", "content": "first"}]
        await client.complete("p1", "second", _MODEL, "sys", messages=messages)
        _, kwargs = pool.request_with_routing.call_args
        # payload must contain both messages (text-folding appends)
        raw = kwargs.get("payload") or pool.request_with_routing.call_args.args[1]
        body = json.loads(raw)
        assert len(body["messages"]) == 2


class TestLlmClientStream:
    @pytest.mark.asyncio
    async def test_stream_yields_text_then_terminal(self) -> None:
        pool = _make_pool()

        async def _gen() -> AsyncIterator:
            yield Ok(_chunk_bytes(delta="hi", done=False))
            yield Ok(_chunk_bytes(delta=None, done=True, duration_ms=10))

        pool.stream_request = MagicMock(return_value=_gen())
        client = _make_client(pool)
        stream = await client.stream("p1", "hello", _MODEL, "sys")
        events = [e async for e in stream]
        assert len(events) == 2
        assert isinstance(events[0], TextLlmEvent)
        assert events[0].text == "hi"
        assert isinstance(events[1], ResultLlmEvent)
        assert events[1].is_error is False

    @pytest.mark.asyncio
    async def test_stream_stops_after_terminal_event(self) -> None:
        pool = _make_pool()

        async def _gen() -> AsyncIterator:
            yield Ok(_chunk_bytes(delta="first", done=False))
            yield Ok(_chunk_bytes(delta=None, done=True))
            yield Ok(_chunk_bytes(delta="never", done=False))

        pool.stream_request = MagicMock(return_value=_gen())
        client = _make_client(pool)
        stream = await client.stream("p1", "x", _MODEL, "sys")
        events = [e async for e in stream]
        # no TextLlmEvent with text="never" after the terminal
        text_events = [
            e for e in events if isinstance(e, TextLlmEvent) and e.text == "never"
        ]
        assert text_events == []

    @pytest.mark.asyncio
    async def test_stream_yields_error_event_on_transport_err(self) -> None:
        pool = _make_pool()
        err = SanitizedError(
            code="transport.timeout", message="TimeoutError", retryable=True
        )

        async def _gen() -> AsyncIterator:
            yield Err(err)

        pool.stream_request = MagicMock(return_value=_gen())
        client = _make_client(pool)
        stream = await client.stream("p1", "x", _MODEL, "sys")
        events = [e async for e in stream]
        assert len(events) == 1
        assert isinstance(events[0], ResultLlmEvent)
        assert events[0].is_error is True

    @pytest.mark.asyncio
    async def test_stream_skips_none_events(self) -> None:
        pool = _make_pool()
        noop_payload: dict[str, Any] = {
            "contract_version": CONTRACT_VERSION,
            "trace_id": "t1",
            "issued_at": datetime.now(timezone.utc).isoformat(),
            "request_id": "req1",
            "delta": None,
            "done": False,
            "is_error": False,
        }

        async def _gen() -> AsyncIterator:
            yield Ok(json.dumps(noop_payload).encode())
            yield Ok(_chunk_bytes(delta=None, done=True))

        pool.stream_request = MagicMock(return_value=_gen())
        client = _make_client(pool)
        stream = await client.stream("p1", "x", _MODEL, "sys")
        events = [e async for e in stream]
        assert len(events) == 1
        assert isinstance(events[0], ResultLlmEvent)


class TestLlmClientStop:
    @pytest.mark.asyncio
    async def test_stop_delegates_to_pool(self) -> None:
        pool = _make_pool()
        client = _make_client(pool)
        await client.stop()
        pool.stop.assert_awaited_once()
