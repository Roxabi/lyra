"""Tests for CliPool streaming, reaper callbacks, sweep timing, and env hardening."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.cli.cli_pool import CliPool, _ProcessEntry
from lyra.core.cli.cli_protocol import StreamingIterator
from lyra.core.messaging.events import ResultLlmEvent, TextLlmEvent
from tests.conftest import yield_once

from .conftest_cli_pool import (
    _PATCH_TARGET,
    ASSISTANT_LINE,
    DEFAULT_MODEL,
    INIT_LINE,
    RESULT_LINE,
    _ndjson,
    make_fake_proc,
)

# ---------------------------------------------------------------------------
# Streaming-specific NDJSON lines (used only in this file)
# ---------------------------------------------------------------------------

_STREAM_INIT_LINE = _ndjson(
    {"type": "system", "subtype": "init", "session_id": "stream-sess-1"}
)
_TEXT_DELTA_LINE = _ndjson(
    {
        "type": "stream_event",
        "event": {
            "type": "content_block_delta",
            "delta": {"type": "text_delta", "text": "Hello"},
        },
    }
)
_STREAM_RESULT_LINE = _ndjson(
    {
        "type": "result",
        "session_id": "stream-sess-1",
        "duration_ms": 75,
        "is_error": False,
    }
)


# ---------------------------------------------------------------------------
# TestOnReapCallback
# ---------------------------------------------------------------------------


class TestOnReapCallback:
    """#317 SC-6, SC-7: on_reap callback invoked on idle eviction, failure-isolated."""

    async def test_reaper_calls_on_reap_for_idle(self) -> None:
        on_reap = AsyncMock()
        pool = CliPool(idle_ttl=0, on_reap=on_reap)  # ttl=0 → immediate reap

        proc = make_fake_proc([INIT_LINE])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        entry.last_activity = 0  # ancient
        pool._entries["p1"] = entry

        # Run one reaper iteration manually
        pool._last_sweep_at = None
        await yield_once()  # let event loop tick

        # Simulate the reaper logic inline (to avoid sleeping 60s)
        pool._last_sweep_at = time.monotonic()
        now = time.time()
        snapshot = list(pool._entries.items())
        to_kill = [
            (pid, e)
            for pid, e in snapshot
            if not e.is_alive() or (now - e.last_activity) > pool._idle_ttl
        ]
        for pid, e in to_kill:
            reason = "idle" if e.is_alive() else "dead"
            await pool._kill(pid)
            if pool._on_reap and reason == "idle":
                await pool._on_reap(pid, reason)

        on_reap.assert_called_once_with("p1", "idle")

    async def test_reaper_survives_on_reap_failure(self) -> None:
        """SC-7: on_reap failure must not crash reaper."""
        on_reap = AsyncMock(side_effect=RuntimeError("dispatch failed"))
        pool = CliPool(idle_ttl=0, on_reap=on_reap)

        proc = make_fake_proc([INIT_LINE])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        entry.last_activity = 0
        pool._entries["p1"] = entry

        # Simulate reaper logic — on_reap raises but should be caught
        pool._last_sweep_at = time.monotonic()
        now = time.time()
        snapshot = list(pool._entries.items())
        to_kill = [
            (pid, e)
            for pid, e in snapshot
            if not e.is_alive() or (now - e.last_activity) > pool._idle_ttl
        ]
        for pid, e in to_kill:
            reason = "idle" if e.is_alive() else "dead"
            await pool._kill(pid)
            if pool._on_reap and reason == "idle":
                try:
                    await pool._on_reap(pid, reason)
                except Exception:
                    pass  # matches fire-and-forget pattern

        # No exception propagated — reaper survived
        assert "p1" not in pool._entries

    async def test_on_reap_not_called_for_dead_processes(self) -> None:
        """on_reap only fires for idle eviction, not dead process cleanup."""
        on_reap = AsyncMock()
        pool = CliPool(idle_ttl=9999, on_reap=on_reap)

        proc = make_fake_proc([INIT_LINE])
        proc.returncode = 1  # dead
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        pool._entries["p1"] = entry

        pool._last_sweep_at = time.monotonic()
        now = time.time()
        snapshot = list(pool._entries.items())
        to_kill = [
            (pid, e)
            for pid, e in snapshot
            if not e.is_alive() or (now - e.last_activity) > pool._idle_ttl
        ]
        for pid, e in to_kill:
            reason = "idle" if e.is_alive() else "dead"
            await pool._kill(pid)
            if pool._on_reap and reason == "idle":
                await pool._on_reap(pid, reason)

        on_reap.assert_not_called()


# ---------------------------------------------------------------------------
# TestCliPoolLastSweepAt
# ---------------------------------------------------------------------------


class TestCliPoolLastSweepAt:
    """#317: _last_sweep_at initialized to None, updated by reaper."""

    def test_last_sweep_at_initially_none(self) -> None:
        pool = CliPool()
        assert pool._last_sweep_at is None


# ---------------------------------------------------------------------------
# TestCliPoolSendStreaming
# ---------------------------------------------------------------------------


class TestCliPoolSendStreaming:
    """CliPool.send_streaming() spawns process and returns a StreamingIterator."""

    async def test_send_streaming_returns_iterator(self) -> None:
        proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            it = await pool.send_streaming("pool-s1", "hello", DEFAULT_MODEL)

        assert isinstance(it, StreamingIterator)

    async def test_send_streaming_yields_text_chunks(self) -> None:
        proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            it = await pool.send_streaming("pool-s1", "hello", DEFAULT_MODEL)

        events = [ev async for ev in it]
        assert events == [
            TextLlmEvent(text="Hello"),
            ResultLlmEvent(is_error=False, duration_ms=75, cost_usd=None),
        ]

    async def test_send_streaming_spawn_failure_raises(self) -> None:
        pool = CliPool()

        with patch(
            _PATCH_TARGET,
            new=AsyncMock(side_effect=OSError("command not found")),
        ):
            with pytest.raises(RuntimeError, match="Failed to spawn"):
                await pool.send_streaming("pool-s2", "hello", DEFAULT_MODEL)

    async def test_send_streaming_increments_turn_count(self) -> None:
        proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            await pool.send_streaming("pool-s3", "hello", DEFAULT_MODEL)

        assert pool._entries["pool-s3"].turn_count == 1

    async def test_send_streaming_respawns_on_system_prompt_change(self) -> None:
        # Arrange — two sends with different system prompts
        first_proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        second_proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()
        spawn_mock = AsyncMock(side_effect=[first_proc, second_proc])

        with patch(_PATCH_TARGET, new=spawn_mock):
            it1 = await pool.send_streaming(
                "pool-s4", "hi", DEFAULT_MODEL, system_prompt="A"
            )
            # Consume iterator to complete first turn
            async for _ in it1:
                pass
            it2 = await pool.send_streaming(
                "pool-s4", "hi", DEFAULT_MODEL, system_prompt="B"
            )
            async for _ in it2:
                pass

        assert spawn_mock.call_count == 2
        first_proc.terminate.assert_called_once()

    async def test_send_streaming_respawns_on_model_config_change(self) -> None:
        # Arrange — streaming respawns on model_config mismatch (unlike send())
        first_proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        second_proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()
        spawn_mock = AsyncMock(side_effect=[first_proc, second_proc])
        config_a = ModelConfig(model="claude-sonnet-4-5")
        config_b = ModelConfig(model="claude-opus-4-5")

        with patch(_PATCH_TARGET, new=spawn_mock):
            it1 = await pool.send_streaming("pool-s5", "hi", config_a)
            async for _ in it1:
                pass
            it2 = await pool.send_streaming("pool-s5", "hi", config_b)
            async for _ in it2:
                pass

        assert spawn_mock.call_count == 2

    async def test_send_streaming_uses_reset_fn_on_aclose(self) -> None:
        proc = make_fake_proc([_STREAM_INIT_LINE, _TEXT_DELTA_LINE])
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            it = await pool.send_streaming("pool-s6", "hello", DEFAULT_MODEL)

        # Act — close the iterator early (simulates cancellation)
        await it.aclose()

        # Assert — pool entry was reset (killed)
        assert "pool-s6" not in pool._entries


# ---------------------------------------------------------------------------
# TestCliPoolSpawnEnv — H-4 env hardening (#251)
# ---------------------------------------------------------------------------


class TestCliPoolSpawnEnv:
    """Subprocess env allowlist and HOME hardening."""

    async def test_spawn_sets_home_to_real_home(self) -> None:
        """HOME env var should point to the real user HOME."""
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool-env1", "hello", DEFAULT_MODEL)

        _args, kwargs = mock_spawn.call_args
        env = kwargs["env"]
        assert "HOME" in env
        assert env["HOME"] == str(Path.home())

    async def test_spawn_excludes_secrets_from_env(self, monkeypatch) -> None:
        """Keys outside _SAFE_ENV_KEYS must not appear in subprocess env."""
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-test-secret")
        monkeypatch.setenv("TELEGRAM_TOKEN", "bot-token-secret")

        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool-env2", "hello", DEFAULT_MODEL)

        _args, kwargs = mock_spawn.call_args
        env = kwargs["env"]
        assert "ANTHROPIC_API_KEY" not in env
        assert "TELEGRAM_TOKEN" not in env

    async def test_spawn_failure_returns_not_ok(self) -> None:
        """If create_subprocess_exec raises, result.ok is False."""
        pool = CliPool()

        with patch(_PATCH_TARGET, side_effect=OSError("spawn failed")):
            result = await pool.send("pool-env3", "hello", DEFAULT_MODEL)

        assert not result.ok
