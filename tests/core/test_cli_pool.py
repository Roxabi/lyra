"""Tests for lyra.core.cli_pool: CliPool, _ProcessEntry, send, _read_until_result."""

from __future__ import annotations

import asyncio
import json
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.agent import ModelConfig
from lyra.core.cli_pool import CliPool, _ProcessEntry

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_fake_proc(stdout_lines: list[bytes]) -> MagicMock:
    """Return a mock Process with controllable stdout readline side-effects."""
    proc = MagicMock()
    proc.returncode = None  # alive
    proc.pid = 99

    # stdin
    proc.stdin = MagicMock()
    proc.stdin.write = MagicMock()
    proc.stdin.drain = AsyncMock(return_value=None)

    # stdout: readline returns lines in order, then b"" for EOF
    lines_with_eof = list(stdout_lines) + [b""]
    proc.stdout = MagicMock()
    proc.stdout.readline = AsyncMock(side_effect=lines_with_eof)

    # termination
    proc.terminate = MagicMock()
    proc.wait = AsyncMock(return_value=0)
    proc.kill = MagicMock()

    return proc


def _ndjson(obj: dict) -> bytes:
    return (json.dumps(obj) + "\n").encode()


DEFAULT_MODEL = ModelConfig()

INIT_LINE = _ndjson(
    {
        "type": "system",
        "subtype": "init",
        "session_id": "sess-1",
        "model": "claude-sonnet-4-5",
    }
)
ASSISTANT_LINE = _ndjson(
    {
        "type": "assistant",
        "message": {"content": [{"type": "text", "text": "Hello from Claude"}]},
    }
)
RESULT_LINE = _ndjson(
    {
        "type": "result",
        "result": "Hello from Claude",
        "session_id": "sess-1",
        "is_error": False,
        "duration_ms": 42,
    }
)

_PATCH_TARGET = "lyra.core.cli_pool.asyncio.create_subprocess_exec"


# ---------------------------------------------------------------------------
# TestCliPoolBuildCmd
# ---------------------------------------------------------------------------


class TestCliPoolBuildCmd:
    def test_basic_cmd(self) -> None:
        pool = CliPool()
        cmd = pool._build_cmd(DEFAULT_MODEL)

        assert cmd[0] == "claude"
        assert "--input-format" in cmd
        assert "stream-json" in cmd
        assert "--model" in cmd
        idx = cmd.index("--model")
        assert cmd[idx + 1] == "claude-sonnet-4-5"
        assert "--max-turns" in cmd
        idx = cmd.index("--max-turns")
        assert cmd[idx + 1] == "10"
        assert "--allowedTools" not in cmd
        assert "--resume" not in cmd

    def test_with_tools(self) -> None:
        pool = CliPool()
        cfg = ModelConfig(tools=("Read", "Grep"))
        cmd = pool._build_cmd(cfg)

        assert "--allowedTools" in cmd
        idx = cmd.index("--allowedTools")
        assert cmd[idx + 1] == "Read,Grep"

    def test_with_session_id(self) -> None:
        pool = CliPool()
        cmd = pool._build_cmd(DEFAULT_MODEL, session_id="abc123")

        assert "--resume" in cmd
        idx = cmd.index("--resume")
        assert cmd[idx + 1] == "abc123"

    def test_no_resume_when_session_id_is_none(self) -> None:
        pool = CliPool()
        cmd = pool._build_cmd(DEFAULT_MODEL, session_id=None)
        assert "--resume" not in cmd


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

        pool = CliPool(default_timeout=1)
        entry = self._make_entry_with_proc(proc)
        result = await pool._read_until_result(entry)

        assert not result.ok
        assert "Timeout" in result.error

    async def test_eof(self) -> None:
        proc = make_fake_proc([b""])  # immediate EOF
        pool = CliPool()
        entry = self._make_entry_with_proc(proc)
        result = await pool._read_until_result(entry)

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
        pool = CliPool()
        entry = self._make_entry_with_proc(proc)
        result = await pool._read_until_result(entry)

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
        pool = CliPool()
        entry = self._make_entry_with_proc(proc)
        result = await pool._read_until_result(entry)

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
        pool = CliPool()
        entry = self._make_entry_with_proc(proc)
        result = await pool._read_until_result(entry)

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
        assert pool._reaper_task is not None
        # Clean up
        reaper = pool._reaper_task
        assert reaper is not None
        reaper.cancel()
        try:
            await reaper  # type: ignore[misc]
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
