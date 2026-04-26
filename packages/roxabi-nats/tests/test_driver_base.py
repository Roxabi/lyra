"""RED-phase tests for NatsDriverBase (issue #941).

NatsDriverBase does not exist yet — all tests are expected to fail with
ImportError until the implementation is in place (T2).

Covers:
- _stream_gen: yields chunks, stops on done=True, terminates on timeout
- is_alive: threshold behaviour (within/outside HB_TTL)
- _on_heartbeat: updates _worker_freshness from JSON msg
- _any_worker_alive: prunes stale entries older than HB_TTL*2
- _request: publishes via nc.request(), returns parsed JSON dict
- start: subscribes to HB_SUBJECT pattern exactly once (idempotent)
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import AsyncIterator
from unittest.mock import AsyncMock, MagicMock

import pytest

from roxabi_nats.driver_base import NatsDriverBase  # ImportError expected (RED)

# ---------------------------------------------------------------------------
# Concrete subclass — minimal stub for testing
# ---------------------------------------------------------------------------

HB_SUBJECT = "lyra.clipool.heartbeat.*"


class _ConcreteDriver(NatsDriverBase):
    HB_SUBJECT: str = HB_SUBJECT


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_mock_nc(*, is_connected: bool = True) -> MagicMock:
    """Return a MagicMock NATS client with async stubs attached."""
    nc = MagicMock()
    nc.is_connected = is_connected
    nc.new_inbox = MagicMock(return_value="_INBOX.test123")
    nc.subscribe = AsyncMock()
    nc.publish = AsyncMock()
    nc.request = AsyncMock()
    return nc


def _make_msg(data: dict) -> MagicMock:
    """Return a mock NATS message whose .data is the JSON-encoded dict."""
    msg = MagicMock()
    msg.data = json.dumps(data).encode("utf-8")
    return msg


# ---------------------------------------------------------------------------
# T_stream — _stream_gen
# ---------------------------------------------------------------------------


class TestStreamGen:
    """_stream_gen yields raw dict chunks until done=True."""

    @pytest.mark.asyncio
    async def test_stream_gen_yields_chunks(self) -> None:
        """Chunks without done=True are yielded in order."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=5.0)

        chunks = [
            {"text": "hello", "done": False},
            {"text": " world", "done": False},
            {"text": "", "done": True},
        ]

        # Capture the subscription callback so we can push messages into the queue.
        captured_cb = None

        async def _fake_subscribe(inbox: str, *, cb) -> MagicMock:
            nonlocal captured_cb
            captured_cb = cb
            sub = MagicMock()
            sub.unsubscribe = AsyncMock()
            return sub

        nc.subscribe = AsyncMock(side_effect=_fake_subscribe)

        async def _inject_chunks(gen: AsyncIterator) -> list[dict]:
            """Advance the generator while feeding messages through the callback."""
            results: list[dict] = []
            # Kick the generator to run until it awaits the first queue.get()
            async for chunk in gen:
                results.append(chunk)
            return results

        # We need to push messages after the generator subscribes.
        # Use a task to inject them concurrently.
        collected: list[dict] = []

        async def _run() -> None:
            nonlocal collected
            gen = driver._stream_gen("lyra.clipool.exec", {"cmd": "echo hi"})

            # Consume the generator in a task; inject chunks from this coroutine.
            async def _consume() -> None:
                nonlocal collected
                async for chunk in gen:
                    collected.append(chunk)

            task = asyncio.create_task(_consume())
            # Give the generator a moment to subscribe and await queue.get()
            await asyncio.sleep(0)
            assert captured_cb is not None, "subscribe callback was never captured"
            for chunk_dict in chunks:
                msg = _make_msg(chunk_dict)
                await captured_cb(msg)
            await task

        await _run()

        # Assert — only chunks before done=True (exclusive) should be yielded,
        # or the generator may include the done chunk depending on impl.
        # We assert that the non-done chunks all arrived.
        texts = [c.get("text") for c in collected]
        assert "hello" in texts
        assert " world" in texts

    @pytest.mark.asyncio
    async def test_stream_gen_stops_on_done(self) -> None:
        """Generator terminates when a chunk with done=True arrives."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=5.0)

        captured_cb = None

        async def _fake_subscribe(inbox: str, *, cb) -> MagicMock:
            nonlocal captured_cb
            captured_cb = cb
            sub = MagicMock()
            sub.unsubscribe = AsyncMock()
            return sub

        nc.subscribe = AsyncMock(side_effect=_fake_subscribe)

        collected: list[dict] = []

        async def _run() -> None:
            gen = driver._stream_gen("lyra.clipool.exec", {"cmd": "ls"})

            async def _consume() -> None:
                async for chunk in gen:
                    collected.append(chunk)

            task = asyncio.create_task(_consume())
            await asyncio.sleep(0)
            assert captured_cb is not None

            # Push a done=True chunk immediately — generator must stop.
            await captured_cb(_make_msg({"done": True}))
            await task

        await _run()

        # Assert — generator returned without yielding extra items after done
        # (collected may include the done chunk or not depending on impl,
        # but the task must have finished cleanly).
        # The key invariant is that the generator did NOT block forever.
        assert True  # reaching here proves the generator exited

    @pytest.mark.asyncio
    async def test_stream_gen_timeout(self) -> None:
        """Generator terminates (yields nothing extra) when queue.get times out."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=0.05)

        sub_mock = MagicMock()
        sub_mock.unsubscribe = AsyncMock()
        nc.subscribe = AsyncMock(return_value=sub_mock)

        collected: list[dict] = []

        # Act — no messages pushed; queue.get will time out after timeout seconds.
        async def _run() -> None:
            async for chunk in driver._stream_gen("lyra.clipool.exec", {"cmd": "ls"}):
                collected.append(chunk)

        await _run()

        # Assert — generator exited cleanly (timeout path), no infinite hang.
        # The unsubscribe finaliser must have been called.
        sub_mock.unsubscribe.assert_awaited()


# ---------------------------------------------------------------------------
# T_is_alive — is_alive threshold
# ---------------------------------------------------------------------------


class TestIsAlive:
    """is_alive returns True only when connected + fresh heartbeat exists."""

    def test_is_alive_true_within_threshold(self) -> None:
        """is_alive returns True when nc.is_connected and freshness within HB_TTL."""
        # Arrange
        nc = _make_mock_nc(is_connected=True)
        driver = _ConcreteDriver(nc, timeout=30.0)
        worker_id = "worker-a"
        # Set freshness to just now (well within HB_TTL=30s)
        driver._worker_freshness[worker_id] = time.monotonic()

        # Act
        result = driver.is_alive(worker_id)

        # Assert
        assert result is True

    def test_is_alive_false_when_entry_older_than_ttl(self) -> None:
        """is_alive returns False when the heartbeat is older than HB_TTL."""
        # Arrange
        nc = _make_mock_nc(is_connected=True)
        driver = _ConcreteDriver(nc, timeout=30.0)
        worker_id = "worker-b"
        # Set freshness to HB_TTL + 1 seconds in the past → stale
        driver._worker_freshness[worker_id] = time.monotonic() - (driver.HB_TTL + 1.0)

        # Act
        result = driver.is_alive(worker_id)

        # Assert
        assert result is False

    def test_is_alive_false_when_not_connected(self) -> None:
        """is_alive returns False when nc.is_connected is False, regardless of freshness."""  # noqa: E501
        # Arrange
        nc = _make_mock_nc(is_connected=False)
        driver = _ConcreteDriver(nc, timeout=30.0)
        worker_id = "worker-c"
        driver._worker_freshness[worker_id] = time.monotonic()

        # Act
        result = driver.is_alive(worker_id)

        # Assert
        assert result is False

    def test_is_alive_false_when_no_workers(self) -> None:
        """is_alive returns False when _worker_freshness is empty."""
        # Arrange
        nc = _make_mock_nc(is_connected=True)
        driver = _ConcreteDriver(nc, timeout=30.0)

        # Act
        result = driver.is_alive("any-worker")

        # Assert
        assert result is False


# ---------------------------------------------------------------------------
# T_on_heartbeat — freshness update
# ---------------------------------------------------------------------------


class TestOnHeartbeat:
    """_on_heartbeat extracts worker_id from JSON and updates _worker_freshness."""

    @pytest.mark.asyncio
    async def test_heartbeat_sub_updates_freshness(self) -> None:
        """_on_heartbeat stores time.monotonic() keyed by worker_id."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        msg = _make_msg({"worker_id": "worker-x", "ts": 1234567890.0})

        # Act
        before = time.monotonic()
        await driver._on_heartbeat(msg)
        after = time.monotonic()

        # Assert
        assert "worker-x" in driver._worker_freshness
        ts = driver._worker_freshness["worker-x"]
        assert before <= ts <= after

    @pytest.mark.asyncio
    async def test_heartbeat_missing_worker_id_does_not_update(self) -> None:
        """_on_heartbeat ignores messages with no worker_id field."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        msg = _make_msg({"ts": 123.0})  # no worker_id

        # Act
        await driver._on_heartbeat(msg)

        # Assert
        assert driver._worker_freshness == {}

    @pytest.mark.asyncio
    async def test_heartbeat_invalid_json_does_not_raise(self) -> None:
        """_on_heartbeat silently ignores non-JSON payloads."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        msg = MagicMock()
        msg.data = b"not json at all"

        # Act / Assert — must not raise
        await driver._on_heartbeat(msg)
        assert driver._worker_freshness == {}

    @pytest.mark.asyncio
    async def test_heartbeat_updates_existing_entry(self) -> None:
        """A second heartbeat for the same worker_id overwrites the previous timestamp."""  # noqa: E501
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        driver._worker_freshness["worker-y"] = time.monotonic() - 10.0

        msg = _make_msg({"worker_id": "worker-y"})

        # Act
        await driver._on_heartbeat(msg)

        # Assert — timestamp was refreshed (newer than 10s ago)
        ts = driver._worker_freshness["worker-y"]
        assert time.monotonic() - ts < 1.0


# ---------------------------------------------------------------------------
# T_any_worker_alive — prune stale entries
# ---------------------------------------------------------------------------


class TestAnyWorkerAlive:
    """_any_worker_alive prunes entries older than HB_TTL*2 and checks recency."""

    def test_any_worker_alive_fresh_entry_returns_true(self) -> None:
        """Returns True when at least one entry is within HB_TTL."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        driver._worker_freshness["fresh"] = time.monotonic()

        # Act
        result = driver._any_worker_alive()

        # Assert
        assert result is True

    def test_any_worker_alive_stale_entry_returns_false(self) -> None:
        """Returns False when the only entry is older than HB_TTL."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        driver._worker_freshness["stale"] = time.monotonic() - (driver.HB_TTL + 5.0)

        # Act
        result = driver._any_worker_alive()

        # Assert
        assert result is False

    def test_any_worker_alive_prunes_stale(self) -> None:
        """Entries older than HB_TTL*2 are evicted from _worker_freshness."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        very_stale_ts = time.monotonic() - (driver.HB_TTL * 2 + 1.0)
        driver._worker_freshness["very-stale"] = very_stale_ts

        # Act
        driver._any_worker_alive()

        # Assert — entry evicted
        assert "very-stale" not in driver._worker_freshness

    def test_any_worker_alive_keeps_within_double_ttl(self) -> None:
        """Entries within HB_TTL*2 (but outside HB_TTL) are kept but not counted as alive."""  # noqa: E501
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        # Between HB_TTL and HB_TTL*2 → kept but stale (not alive)
        between_ts = time.monotonic() - (driver.HB_TTL + 1.0)
        driver._worker_freshness["between"] = between_ts

        # Act
        result = driver._any_worker_alive()

        # Assert — not counted as alive, but also not yet evicted
        assert result is False
        assert "between" in driver._worker_freshness

    def test_any_worker_alive_empty_freshness_returns_false(self) -> None:
        """Returns False when no workers are registered."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        # Act
        result = driver._any_worker_alive()

        # Assert
        assert result is False


# ---------------------------------------------------------------------------
# T_request — simple request-reply
# ---------------------------------------------------------------------------


class TestRequest:
    """_request dispatches via nc.request() and returns parsed JSON."""

    @pytest.mark.asyncio
    async def test_request_returns_parsed_json(self) -> None:
        """_request publishes to nc.request and returns the parsed reply dict."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        reply_data = {"status": "ok", "result": "pong"}
        mock_reply = MagicMock()
        mock_reply.data = json.dumps(reply_data).encode("utf-8")
        nc.request = AsyncMock(return_value=mock_reply)

        # Act
        result = await driver._request("lyra.clipool.ping", {"msg": "ping"})

        # Assert
        assert result == reply_data
        nc.request.assert_awaited_once()
        call_args = nc.request.call_args
        assert call_args.args[0] == "lyra.clipool.ping"

    @pytest.mark.asyncio
    async def test_request_sends_json_encoded_payload(self) -> None:
        """_request JSON-encodes the payload_dict and passes it to nc.request."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        payload = {"key": "value", "num": 42}
        mock_reply = MagicMock()
        mock_reply.data = b'{"ok": true}'
        nc.request = AsyncMock(return_value=mock_reply)

        # Act
        await driver._request("lyra.clipool.exec", payload)

        # Assert — the bytes sent are valid JSON matching the payload
        sent_bytes: bytes = nc.request.call_args.args[1]
        sent_dict = json.loads(sent_bytes)
        assert sent_dict == payload

    @pytest.mark.asyncio
    async def test_request_uses_instance_timeout_by_default(self) -> None:
        """_request uses self._timeout when no explicit timeout is given."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=42.0)

        mock_reply = MagicMock()
        mock_reply.data = b"{}"
        nc.request = AsyncMock(return_value=mock_reply)

        # Act
        await driver._request("lyra.clipool.exec", {})

        # Assert
        call_kwargs = nc.request.call_args.kwargs
        assert call_kwargs.get("timeout") == 42.0

    @pytest.mark.asyncio
    async def test_request_allows_custom_timeout(self) -> None:
        """_request accepts an explicit timeout kwarg that overrides self._timeout."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        mock_reply = MagicMock()
        mock_reply.data = b"{}"
        nc.request = AsyncMock(return_value=mock_reply)

        # Act
        await driver._request("lyra.clipool.exec", {}, timeout=5.0)

        # Assert
        call_kwargs = nc.request.call_args.kwargs
        assert call_kwargs.get("timeout") == 5.0


# ---------------------------------------------------------------------------
# T_start — idempotent subscribe
# ---------------------------------------------------------------------------


class TestStart:
    """start() subscribes to the heartbeat pattern exactly once, regardless of calls."""

    @pytest.mark.asyncio
    async def test_start_subscribes_once(self) -> None:
        """Calling start() twice only creates one subscription."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        sub_mock = MagicMock()
        nc.subscribe = AsyncMock(return_value=sub_mock)

        # Act
        await driver.start()
        await driver.start()

        # Assert — subscribe called only once despite two start() calls
        nc.subscribe.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_start_subscribes_to_hb_subject(self) -> None:
        """start() subscribes to the class-level HB_SUBJECT pattern."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        sub_mock = MagicMock()
        nc.subscribe = AsyncMock(return_value=sub_mock)

        # Act
        await driver.start()

        # Assert
        call_args = nc.subscribe.call_args
        subscribed_subject = call_args.args[0]
        assert subscribed_subject == HB_SUBJECT

    @pytest.mark.asyncio
    async def test_start_stores_subscription(self) -> None:
        """start() stores the subscription object for later stop() calls."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        sub_mock = MagicMock()
        nc.subscribe = AsyncMock(return_value=sub_mock)

        # Act
        await driver.start()

        # Assert — _hb_sub is set
        assert driver._hb_sub is not None

    @pytest.mark.asyncio
    async def test_stop_unsubscribes(self) -> None:
        """stop() calls unsubscribe() on the stored subscription."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)

        sub_mock = MagicMock()
        sub_mock.unsubscribe = AsyncMock()
        nc.subscribe = AsyncMock(return_value=sub_mock)

        await driver.start()

        # Act
        await driver.stop()

        # Assert
        sub_mock.unsubscribe.assert_awaited_once()
        assert driver._hb_sub is None

    @pytest.mark.asyncio
    async def test_stop_is_idempotent(self) -> None:
        """Calling stop() twice does not raise even when _hb_sub is None."""
        # Arrange
        nc = _make_mock_nc()
        driver = _ConcreteDriver(nc, timeout=30.0)
        # _hb_sub is None — never started

        # Act / Assert — must not raise
        await driver.stop()
        await driver.stop()


# ---------------------------------------------------------------------------
# T_construction — basic initialisation checks
# ---------------------------------------------------------------------------


class TestConstruction:
    """NatsDriverBase stores nc and timeout; initialises freshness dict and hb_sub."""

    def test_stores_nc_and_timeout(self) -> None:
        """Constructor stores nc and timeout on the instance."""
        # Arrange
        nc = _make_mock_nc()

        # Act
        driver = _ConcreteDriver(nc, timeout=45.0)

        # Assert
        assert driver._nc is nc
        assert driver._timeout == 45.0

    def test_default_timeout(self) -> None:
        """Default timeout is 120.0 seconds."""
        # Arrange
        nc = _make_mock_nc()

        # Act
        driver = _ConcreteDriver(nc)

        # Assert
        assert driver._timeout == 120.0

    def test_initial_freshness_is_empty(self) -> None:
        """_worker_freshness starts as an empty dict."""
        # Arrange
        nc = _make_mock_nc()

        # Act
        driver = _ConcreteDriver(nc)

        # Assert
        assert driver._worker_freshness == {}

    def test_hb_sub_is_none_initially(self) -> None:
        """_hb_sub is None before start() is called."""
        # Arrange
        nc = _make_mock_nc()

        # Act
        driver = _ConcreteDriver(nc)

        # Assert
        assert driver._hb_sub is None

    def test_hb_ttl_class_constant(self) -> None:
        """HB_TTL class constant is 30.0."""
        # Arrange / Act / Assert
        assert _ConcreteDriver.HB_TTL == 30.0
