"""Tests for CliPool process lifecycle: build_cmd, send, read_until_result, lifecycle, is_alive."""  # noqa: E501

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.agent_config import ModelConfig
from lyra.core.cli_pool import CliPool, _ProcessEntry
from lyra.core.cli_protocol import read_until_result

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
# TestCliPoolBuildCmd
# ---------------------------------------------------------------------------


class TestCliPoolBuildCmd:
    def test_basic_cmd(self) -> None:
        pool = CliPool()
        cmd, prompt_file = pool._build_cmd(DEFAULT_MODEL)

        assert cmd[0] == "claude"
        assert "--input-format" in cmd
        assert "stream-json" in cmd
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-opus-4-6"
        # max_turns=None (default) → unlimited, --max-turns flag omitted
        assert "--max-turns" not in cmd
        assert "--allowedTools" not in cmd
        assert "--resume" not in cmd
        assert prompt_file is None

    def test_explicit_max_turns(self) -> None:
        pool = CliPool()
        cfg = ModelConfig(max_turns=10)
        cmd, prompt_file = pool._build_cmd(cfg)

        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "10"
        assert prompt_file is None

    def test_with_tools(self) -> None:
        pool = CliPool()
        cfg = ModelConfig(tools=("Read", "Grep"))
        cmd, prompt_file = pool._build_cmd(cfg)

        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Read,Grep"
        assert prompt_file is None

    def test_with_session_id(self) -> None:
        pool = CliPool()
        cmd, prompt_file = pool._build_cmd(DEFAULT_MODEL, session_id="abc123")

        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "abc123"
        assert prompt_file is None

    def test_no_resume_when_session_id_is_none(self) -> None:
        pool = CliPool()
        cmd, prompt_file = pool._build_cmd(DEFAULT_MODEL, session_id=None)
        assert "--resume" not in cmd
        assert prompt_file is None

    def test_system_prompt_included_when_provided(self) -> None:
        import os
        from pathlib import Path

        pool = CliPool()
        cmd, prompt_file = pool._build_cmd(DEFAULT_MODEL, system_prompt="You are helpful.")
        try:
            assert "--system-prompt-file" in cmd
            idx = cmd.index("--system-prompt-file")
            assert cmd[idx + 1] == prompt_file
            assert prompt_file is not None
            assert Path(prompt_file).exists()
            assert Path(prompt_file).read_text() == "You are helpful."
            assert oct(Path(prompt_file).stat().st_mode & 0o777) == oct(0o600)
        finally:
            if prompt_file:
                os.unlink(prompt_file)

    def test_system_prompt_omitted_when_empty(self) -> None:
        pool = CliPool()
        cmd, prompt_file = pool._build_cmd(DEFAULT_MODEL, system_prompt="")
        assert "--system-prompt" not in cmd
        assert "--system-prompt-file" not in cmd
        assert prompt_file is None


# ---------------------------------------------------------------------------
# TestCliPoolSend
# ---------------------------------------------------------------------------


class TestCliPoolSend:
    async def test_send_happy_path(self) -> None:
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            result = await pool.send("pool-1", "hello", DEFAULT_MODEL)

        assert result.result == "Hello from Claude"
        assert result.session_id == "sess-1"
        assert result.ok

    async def test_send_respawns_dead_process(self) -> None:
        # First proc — dead (returncode set to 1)
        dead_proc = make_fake_proc([])
        dead_proc.returncode = 1

        # New proc spawned after death
        fresh_proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])

        pool = CliPool()
        # Pre-populate _entries with a dead process entry
        dead_entry = _ProcessEntry(
            proc=dead_proc, pool_id="pool-1", model_config=DEFAULT_MODEL
        )
        pool._entries["pool-1"] = dead_entry

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=fresh_proc)):
            result = await pool.send("pool-1", "hello", DEFAULT_MODEL)

        assert result.result == "Hello from Claude"

    async def test_send_spawn_failure_returns_error(self) -> None:
        pool = CliPool()

        with patch(
            _PATCH_TARGET,
            new=AsyncMock(side_effect=OSError("command not found")),
        ):
            result = await pool.send("pool-1", "hello", DEFAULT_MODEL)

        assert not result.ok
        assert "Failed to spawn" in result.error

    async def test_send_drain_timeout_returns_error_and_kills_entry(self) -> None:
        """stdin.drain() timeout must kill the corrupted entry and return an error."""
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        proc.stdin.drain = AsyncMock(side_effect=asyncio.TimeoutError)
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            result = await pool.send("pool-drain", "hello", DEFAULT_MODEL)

        assert not result.ok
        assert "writing to subprocess stdin" in result.error
        # Entry must be removed so the corrupted process is not reused
        assert "pool-drain" not in pool._entries

    async def test_send_system_prompt_change_respawns(self) -> None:
        """Changing system_prompt between sends must kill old + spawn new process."""
        first_proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        second_proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])

        pool = CliPool()
        spawn_mock = AsyncMock(side_effect=[first_proc, second_proc])

        with patch(_PATCH_TARGET, new=spawn_mock):
            r1 = await pool.send("pool-1", "hi", DEFAULT_MODEL, system_prompt="A")
            assert r1.ok

            r2 = await pool.send("pool-1", "hi", DEFAULT_MODEL, system_prompt="B")
            assert r2.ok

        # Two spawns: original + respawn after prompt change
        assert spawn_mock.call_count == 2
        # Old process must have been terminated
        first_proc.terminate.assert_called_once()

    async def test_send_model_config_mismatch_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        # Provide enough lines for two sends
        proc = make_fake_proc(
            [
                INIT_LINE,
                ASSISTANT_LINE,
                RESULT_LINE,
                INIT_LINE,
                ASSISTANT_LINE,
                RESULT_LINE,
            ]
        )
        pool = CliPool()

        config_a = ModelConfig(model="claude-sonnet-4-5")
        config_b = ModelConfig(model="claude-opus-4-5")

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            await pool.send("pool-1", "first", config_a)

            with caplog.at_level(logging.WARNING, logger="lyra.core.cli_pool"):
                await pool.send("pool-1", "second", config_b)

        assert any("mismatch" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# TestReadUntilResult
# ---------------------------------------------------------------------------


class TestReadUntilResult:
    def _make_entry_with_proc(self, proc: MagicMock) -> _ProcessEntry:
        return _ProcessEntry(proc=proc, pool_id="pool-test", model_config=DEFAULT_MODEL)

    async def test_timeout(self) -> None:
        proc = MagicMock()
        proc.returncode = None
        proc.stdout = MagicMock()
        # readline raises TimeoutError to simulate timeout
        proc.stdout.readline = AsyncMock(side_effect=asyncio.TimeoutError)

        entry = self._make_entry_with_proc(proc)
        result = await read_until_result(entry, pool_id="pool-test", default_timeout=1)

        assert not result.ok
        assert "Timeout" in result.error

    async def test_eof(self) -> None:
        proc = make_fake_proc([b""])  # immediate EOF
        entry = self._make_entry_with_proc(proc)
        result = await read_until_result(entry, pool_id="pool-test")

        assert not result.ok
        assert "terminated" in result.error.lower()

    async def test_error_max_turns(self) -> None:
        result_line = _ndjson(
            {
                "type": "result",
                "result": "Partial answer",
                "session_id": "sess-x",
                "is_error": True,
                "subtype": "error_max_turns",
                "duration_ms": 0,
            }
        )
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, result_line])
        entry = self._make_entry_with_proc(proc)
        result = await read_until_result(entry, pool_id="pool-test")

        assert result.result is not None
        assert result.warning != ""
        warning = result.warning.lower()
        assert "truncated" in warning or "max turns" in warning

    async def test_error_other(self) -> None:
        result_line = _ndjson(
            {
                "type": "result",
                "result": "Something failed",
                "session_id": "sess-x",
                "is_error": True,
                "subtype": "other_error",
                "duration_ms": 0,
            }
        )
        proc = make_fake_proc([INIT_LINE, result_line])
        entry = self._make_entry_with_proc(proc)
        result = await read_until_result(entry, pool_id="pool-test")

        assert not result.ok
        assert result.result == ""

    async def test_result_text_not_clobbered_by_empty_result(self) -> None:
        """If result event has empty 'result', earlier assistant text is preserved."""
        result_line = _ndjson(
            {
                "type": "result",
                "result": "",  # empty — should not overwrite assistant text
                "session_id": "sess-1",
                "is_error": False,
                "duration_ms": 0,
            }
        )
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, result_line])
        entry = self._make_entry_with_proc(proc)
        result = await read_until_result(entry, pool_id="pool-test")

        # The assistant block text "Hello from Claude" must be preserved
        assert result.result == "Hello from Claude"


# ---------------------------------------------------------------------------
# TestCliPoolLifecycle
# ---------------------------------------------------------------------------


class TestCliPoolLifecycle:
    async def test_start_creates_reaper(self) -> None:
        pool = CliPool()
        assert pool._reaper_task is None
        await pool.start()
        # Use getattr to bypass pyright's narrowing from prior `is None` assert
        reaper_task: asyncio.Task[None] | None = getattr(pool, "_reaper_task")
        assert reaper_task is not None
        # Clean up
        reaper_task.cancel()
        try:
            await reaper_task
        except asyncio.CancelledError:
            pass

    async def test_stop_cancels_reaper_and_kills_entries(self) -> None:
        proc = make_fake_proc([])
        pool = CliPool()
        await pool.start()

        entry = _ProcessEntry(
            proc=proc, pool_id="pool-stop", model_config=DEFAULT_MODEL
        )
        pool._entries["pool-stop"] = entry

        await pool.stop()

        assert pool._reaper_task is None or pool._reaper_task.cancelled()
        assert "pool-stop" not in pool._entries
        # terminate() should have been called since proc.returncode is None (alive)
        proc.terminate.assert_called_once()

    async def test_reset_removes_entry(self) -> None:
        proc = make_fake_proc([])
        pool = CliPool()

        entry = _ProcessEntry(
            proc=proc, pool_id="pool-reset", model_config=DEFAULT_MODEL
        )
        pool._entries["pool-reset"] = entry

        await pool.reset("pool-reset")

        assert "pool-reset" not in pool._entries

    async def test_kill_noop_for_unknown_pool(self) -> None:
        pool = CliPool()
        # Should not raise even if the pool_id does not exist
        await pool._kill("nonexistent")
        assert "nonexistent" not in pool._entries


# ---------------------------------------------------------------------------
# T10 — CliPool.is_alive() basic tests
# ---------------------------------------------------------------------------


class TestCliPoolIsAlive:
    """CliPool.is_alive() returns correct liveness state (T10)."""

    def test_returns_false_for_unknown_pool(self) -> None:
        """is_alive() returns False when no entry exists for pool_id."""
        pool = CliPool()
        assert pool.is_alive("nonexistent") is False

    def test_returns_true_for_live_process(self) -> None:
        """is_alive() returns True when an entry has proc.returncode is None."""
        pool = CliPool()
        proc = MagicMock()
        proc.returncode = None  # still running
        entry = _ProcessEntry(
            proc=proc, pool_id="test-pool", model_config=DEFAULT_MODEL
        )
        pool._entries["test-pool"] = entry
        assert pool.is_alive("test-pool") is True

    def test_returns_false_for_dead_process(self) -> None:
        """is_alive() returns False when an entry has proc.returncode != None."""
        pool = CliPool()
        proc = MagicMock()
        proc.returncode = 1  # exited
        entry = _ProcessEntry(
            proc=proc, pool_id="test-pool", model_config=DEFAULT_MODEL
        )
        pool._entries["test-pool"] = entry
        assert pool.is_alive("test-pool") is False
