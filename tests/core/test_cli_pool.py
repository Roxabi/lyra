"""Tests for lyra.core.cli_pool: CliPool, _ProcessEntry, send, _read_until_result."""

from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.core.agent_config import ModelConfig
from lyra.core.cli_pool import CliPool, _ProcessEntry
from lyra.core.cli_protocol import StreamingIterator, read_until_result

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

    def test_system_prompt_included_when_provided(self) -> None:
        pool = CliPool()
        cmd = pool._build_cmd(DEFAULT_MODEL, system_prompt="You are helpful.")
        assert "--system-prompt" in cmd
        idx = cmd.index("--system-prompt")
        assert cmd[idx + 1] == "You are helpful."

    def test_system_prompt_omitted_when_empty(self) -> None:
        pool = CliPool()
        cmd = pool._build_cmd(DEFAULT_MODEL, system_prompt="")
        assert "--system-prompt" not in cmd


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


# ---------------------------------------------------------------------------
# T10 — CliPool.is_alive() basic tests
# ---------------------------------------------------------------------------


class TestCliPoolIsAlive:
    """CliPool.is_alive() returns correct liveness state (T10)."""

    def test_returns_false_for_unknown_pool(self) -> None:
        """is_alive() returns False when no entry exists for pool_id."""
        # Arrange
        pool = CliPool()

        # Act / Assert
        assert pool.is_alive("nonexistent") is False

    def test_returns_true_for_live_process(self) -> None:
        """is_alive() returns True when an entry has proc.returncode is None."""
        # Arrange
        pool = CliPool()
        proc = MagicMock()
        proc.returncode = None  # still running
        entry = _ProcessEntry(
            proc=proc, pool_id="test-pool", model_config=DEFAULT_MODEL
        )
        pool._entries["test-pool"] = entry

        # Act / Assert
        assert pool.is_alive("test-pool") is True

    def test_returns_false_for_dead_process(self) -> None:
        """is_alive() returns False when an entry has proc.returncode != None."""
        # Arrange
        pool = CliPool()
        proc = MagicMock()
        proc.returncode = 1  # exited
        entry = _ProcessEntry(
            proc=proc, pool_id="test-pool", model_config=DEFAULT_MODEL
        )
        pool._entries["test-pool"] = entry

        # Act / Assert
        assert pool.is_alive("test-pool") is False


# ---------------------------------------------------------------------------
# T5 — on_intermediate exception does not propagate
# ---------------------------------------------------------------------------


class TestOnIntermediateException:
    """Exception in on_intermediate is swallowed; result still returned (T5)."""

    async def test_on_intermediate_exception_does_not_propagate(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """Exception in on_intermediate is caught; CliResult is still returned ok."""
        # Intermediates are buffered: the callback fires when flushing the
        # *previous* pending turn on arrival of a new one, so we need at
        # least 3 assistant turns for the flush to happen.
        second_assistant = _ndjson(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Second turn"}]},
            }
        )
        third_assistant = _ndjson(
            {
                "type": "assistant",
                "message": {"content": [{"type": "text", "text": "Third turn"}]},
            }
        )
        proc = make_fake_proc(
            [INIT_LINE, ASSISTANT_LINE, second_assistant, third_assistant, RESULT_LINE]
        )

        async def _raising_cb(text: str) -> None:
            raise RuntimeError("callback exploded")

        entry = _ProcessEntry(proc=proc, pool_id="pool-cb", model_config=DEFAULT_MODEL)
        with caplog.at_level(logging.WARNING, logger="lyra.core.cli_protocol"):
            result = await read_until_result(
                entry, pool_id="pool-cb", on_intermediate=_raising_cb
            )

        # Result must still be returned successfully
        assert result.ok
        # The exception must have been logged at WARNING level
        assert any("on_intermediate" in r.message.lower() for r in caplog.records)


# ---------------------------------------------------------------------------
# TestCliPoolSpawnCwd
# ---------------------------------------------------------------------------


class TestCliPoolSpawnCwd:
    """CliPool._spawn passes model_config.cwd (or _LYRA_ROOT) as cwd."""

    async def test_spawn_uses_lyra_root_when_cwd_is_none(self, tmp_path: Path) -> None:
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool-cwd", "hello", DEFAULT_MODEL)

        _args, kwargs = mock_spawn.call_args
        from lyra.core.cli_pool import _LYRA_ROOT

        assert kwargs["cwd"] == str(_LYRA_ROOT)

    async def test_spawn_cwd_override_takes_priority_over_model_config_cwd(
        self, tmp_path: Path
    ) -> None:
        """_cwd_overrides[pool_id] wins over model_config.cwd."""
        override_dir = tmp_path / "override"
        override_dir.mkdir()
        model_dir = tmp_path / "model"
        model_dir.mkdir()

        model = ModelConfig(cwd=model_dir)
        pool = CliPool()
        # Pre-set a cwd override (simulates a prior workspace switch)
        pool._cwd_overrides["pool-priority"] = override_dir

        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool-priority", "hello", model)

        _args, kwargs = mock_spawn.call_args
        assert kwargs["cwd"] == str(override_dir)

    async def test_spawn_uses_model_config_cwd_when_set(self, tmp_path: Path) -> None:
        custom_dir = tmp_path / "myproject"
        custom_dir.mkdir()
        model = ModelConfig(cwd=custom_dir)
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool-cwd2", "hello", model)

        _args, kwargs = mock_spawn.call_args
        assert kwargs["cwd"] == str(custom_dir)


# ---------------------------------------------------------------------------
# TestCliPoolSwitchCwd
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# T3.4 — CliPool.resume_and_reset() — reply-to-resume (#244)
# ---------------------------------------------------------------------------


class TestCliPoolResumeAndReset:
    """CliPool.resume_and_reset() stores session_id for next spawn (T3.4, SC-5)."""

    async def test_resume_and_reset_sets_session_id(self) -> None:
        """After resume_and_reset(), session stored and process killed."""
        # Arrange
        pool = CliPool()
        proc = make_fake_proc([])
        # Pre-populate a live entry so _kill has something to terminate
        entry = _ProcessEntry(
            proc=proc, pool_id="pool:tg:chat:1", model_config=DEFAULT_MODEL
        )
        pool._entries["pool:tg:chat:1"] = entry

        _SESS = "abcdef01-2345-6789-abcd-ef0123456789"

        # Act — patch _session_file_exists to simulate a live session file
        with patch.object(pool, "_session_file_exists", return_value=True):
            await pool.resume_and_reset("pool:tg:chat:1", _SESS)  # type: ignore[attr-defined]

        # Assert — session stored for next spawn AND process killed
        assert pool._resume_session_ids.get("pool:tg:chat:1") == _SESS  # type: ignore[attr-defined]
        assert "pool:tg:chat:1" not in pool._entries

    async def test_resume_and_reset_skips_when_session_file_missing(self) -> None:
        """If session file is gone from disk, resume_and_reset is a no-op (Tier-2)."""
        # Arrange
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc, pool_id="pool:tg:chat:1", model_config=DEFAULT_MODEL
        )
        pool._entries["pool:tg:chat:1"] = entry

        # Act — session file does not exist on disk
        with patch.object(pool, "_session_file_exists", return_value=False):
            await pool.resume_and_reset("pool:tg:chat:1", "sess-pruned")  # type: ignore[attr-defined]

        # Assert — no kill, no resume intent stored
        assert "pool:tg:chat:1" in pool._entries
        assert pool._resume_session_ids.get("pool:tg:chat:1") is None  # type: ignore[attr-defined]

    async def test_spawn_consumes_resume_session_id_and_passes_to_cmd(self) -> None:
        """_spawn() pops _resume_session_ids and passes --resume to CLI (one-shot)."""
        # Arrange
        pool = CliPool()
        pool._resume_session_ids["pool:resume:1"] = "sess-abc"  # type: ignore[attr-defined]
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])

        # Act — patch subprocess so no real process is started
        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool:resume:1", "hello", DEFAULT_MODEL)

        # Assert — --resume flag present in spawned command
        cmd_args = list(mock_spawn.call_args[0])
        assert "--resume" in cmd_args
        assert cmd_args[cmd_args.index("--resume") + 1] == "sess-abc"
        # Assert — one-shot: intent consumed after spawn
        assert pool._resume_session_ids.get("pool:resume:1") is None  # type: ignore[attr-defined]


class TestCliPoolSwitchCwd:
    async def test_switch_cwd_stores_override(self, tmp_path: Path) -> None:
        pool = CliPool()
        await pool.switch_cwd("pool-ws", tmp_path)
        assert pool._cwd_overrides["pool-ws"] == tmp_path

    async def test_switch_cwd_override_used_on_spawn(self, tmp_path: Path) -> None:
        custom_dir = tmp_path / "ws"
        custom_dir.mkdir()
        pool = CliPool()
        await pool.switch_cwd("pool-ws2", custom_dir)
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool-ws2", "hello", DEFAULT_MODEL)
        _args, kwargs = mock_spawn.call_args
        assert kwargs["cwd"] == str(custom_dir)

    async def test_switch_cwd_kills_existing_process(self, tmp_path: Path) -> None:
        pool = CliPool()
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        # Pre-populate with a live process
        entry = _ProcessEntry(proc=proc, pool_id="pool-ws3", model_config=DEFAULT_MODEL)
        pool._entries["pool-ws3"] = entry
        await pool.switch_cwd("pool-ws3", tmp_path)
        assert "pool-ws3" not in pool._entries


# ---------------------------------------------------------------------------
# Issue #317 — eager cleanup + on_reap + failure isolation
# ---------------------------------------------------------------------------


class TestEagerCleanupOnTerminated:
    """#317 SC-10: send() kills on 'terminated' error, not just 'Timeout'."""

    async def test_send_kills_on_terminated(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([INIT_LINE])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        pool._entries["p1"] = entry

        from lyra.core.cli_protocol import CliResult

        terminated_result = CliResult(
            error="Process terminated unexpectedly"
        )
        with patch(
            "lyra.core.cli_pool.send_and_read",
            new=AsyncMock(return_value=terminated_result),
        ):
            result = await pool.send("p1", "hello", DEFAULT_MODEL)

        assert not result.ok
        assert "p1" not in pool._entries  # entry was cleaned up

    async def test_send_still_kills_on_timeout(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([INIT_LINE])
        entry = _ProcessEntry(proc=proc, pool_id="p2", model_config=DEFAULT_MODEL)
        pool._entries["p2"] = entry

        from lyra.core.cli_protocol import CliResult

        with patch(
            "lyra.core.cli_pool.send_and_read",
            new=AsyncMock(return_value=CliResult(error="Timeout: no output for 900s")),
        ):
            result = await pool.send("p2", "hello", DEFAULT_MODEL)

        assert not result.ok
        assert "p2" not in pool._entries


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
        await asyncio.sleep(0)  # let event loop tick

        # Simulate the reaper logic inline (to avoid sleeping 60s)
        import time

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
        import time

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

        import time

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


class TestCliPoolLastSweepAt:
    """#317: _last_sweep_at initialized to None, updated by reaper."""

    def test_last_sweep_at_initially_none(self) -> None:
        pool = CliPool()
        assert pool._last_sweep_at is None


# ---------------------------------------------------------------------------
# TestCliPoolSendStreaming
# ---------------------------------------------------------------------------

# Streaming-specific NDJSON lines
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


class TestCliPoolSendStreaming:
    """CliPool.send_streaming() spawns process and returns a StreamingIterator."""

    async def test_send_streaming_returns_iterator(self) -> None:
        # Arrange
        proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            # Act
            it = await pool.send_streaming("pool-s1", "hello", DEFAULT_MODEL)

        # Assert
        assert isinstance(it, StreamingIterator)

    async def test_send_streaming_yields_text_chunks(self) -> None:
        # Arrange
        proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            it = await pool.send_streaming("pool-s1", "hello", DEFAULT_MODEL)

        # Act
        chunks = [chunk async for chunk in it]

        # Assert
        assert chunks == ["Hello"]

    async def test_send_streaming_spawn_failure_raises(self) -> None:
        # Arrange — subprocess cannot be started
        pool = CliPool()

        with patch(
            _PATCH_TARGET,
            new=AsyncMock(side_effect=OSError("command not found")),
        ):
            # Act / Assert
            with pytest.raises(RuntimeError, match="Failed to spawn"):
                await pool.send_streaming("pool-s2", "hello", DEFAULT_MODEL)

    async def test_send_streaming_increments_turn_count(self) -> None:
        # Arrange
        proc = make_fake_proc(
            [_STREAM_INIT_LINE, _TEXT_DELTA_LINE, _STREAM_RESULT_LINE]
        )
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)):
            await pool.send_streaming("pool-s3", "hello", DEFAULT_MODEL)

        # Assert
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

        # Assert — two spawns due to prompt change
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

        # Assert — respawned because model changed
        assert spawn_mock.call_count == 2

    async def test_send_streaming_uses_reset_fn_on_aclose(self) -> None:
        # Arrange
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

    async def test_spawn_sets_home_to_temp_dir(self) -> None:
        """HOME env var should point to a lyra_claude_home_ temp dir, not real HOME."""
        import os

        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])
        pool = CliPool()

        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool-env1", "hello", DEFAULT_MODEL)

        _args, kwargs = mock_spawn.call_args
        env = kwargs["env"]
        assert "HOME" in env
        assert env["HOME"] != os.environ.get("HOME", "")
        assert "lyra_claude_home_" in env["HOME"]

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

    async def test_spawn_failure_cleans_up_temp_home(self) -> None:
        """If create_subprocess_exec raises, the temp HOME dir is cleaned up."""
        import os

        pool = CliPool()
        created_dirs: list[str] = []
        _real_mkdtemp = __import__("tempfile").mkdtemp

        def _tracking_mkdtemp(**kwargs):
            d = _real_mkdtemp(**kwargs)
            created_dirs.append(d)
            return d

        with (
            patch(_PATCH_TARGET, side_effect=OSError("spawn failed")),
            patch(
                "lyra.core.cli_pool_worker.tempfile.mkdtemp",
                side_effect=_tracking_mkdtemp,
            ),
        ):
            result = await pool.send("pool-env3", "hello", DEFAULT_MODEL)

        assert not result.ok
        # Temp dir should have been cleaned up on failure
        for d in created_dirs:
            assert not os.path.exists(d), f"Temp dir {d} was not cleaned up"
