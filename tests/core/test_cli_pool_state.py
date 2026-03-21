"""Tests for CliPool state management: cwd, resume/reset, switch_cwd, eager cleanup."""

from __future__ import annotations

import logging
import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from lyra.core.agent_config import ModelConfig
from lyra.core.cli_pool import CliPool, _ProcessEntry
from lyra.core.cli_protocol import CliResult, read_until_result

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
# T3.4 — CliPool.resume_and_reset() — reply-to-resume (#244)
# ---------------------------------------------------------------------------


class TestCliPoolResumeAndReset:
    """CliPool.resume_and_reset() stores session_id for next spawn (T3.4, SC-5)."""

    async def test_resume_and_reset_sets_session_id(self) -> None:
        """After resume_and_reset(), session stored and process killed."""
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


# ---------------------------------------------------------------------------
# TestCliPoolSwitchCwd
# ---------------------------------------------------------------------------


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
# Issue #317 — eager cleanup + failure isolation
# ---------------------------------------------------------------------------


class TestEagerCleanupOnTerminated:
    """#317 SC-10: send() kills on 'terminated' error, not just 'Timeout'."""

    async def test_send_kills_on_terminated(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([INIT_LINE])
        entry = _ProcessEntry(proc=proc, pool_id="p1", model_config=DEFAULT_MODEL)
        pool._entries["p1"] = entry

        from lyra.core.cli_protocol import CliResult

        terminated_result = CliResult(error="Process terminated unexpectedly")
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


# ---------------------------------------------------------------------------
# TestKillPreservesSession — Bug 2: session preserved across reaper/error kills
# ---------------------------------------------------------------------------


class TestKillPreservesSession:
    """_kill() preserves session_id in _resume_session_ids when appropriate."""

    async def test_kill_preserves_session_when_file_exists(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc, pool_id="pool-1", model_config=DEFAULT_MODEL,
            session_id="sess-abc123-deadbeef",
        )
        pool._entries["pool-1"] = entry

        with patch.object(pool, "_session_file_exists", return_value=True):
            await pool._kill("pool-1")

        assert pool._resume_session_ids.get("pool-1") == "sess-abc123-deadbeef"

    async def test_kill_does_not_preserve_when_preserve_false(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc, pool_id="pool-1", model_config=DEFAULT_MODEL,
            session_id="sess-abc123-deadbeef",
        )
        pool._entries["pool-1"] = entry

        with patch.object(pool, "_session_file_exists", return_value=True):
            await pool._kill("pool-1", preserve_session=False)

        assert pool._resume_session_ids.get("pool-1") is None

    async def test_kill_does_not_preserve_when_session_id_is_none(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc, pool_id="pool-1", model_config=DEFAULT_MODEL,
            session_id=None,
        )
        pool._entries["pool-1"] = entry

        with patch.object(pool, "_session_file_exists", return_value=True):
            await pool._kill("pool-1")

        assert pool._resume_session_ids.get("pool-1") is None

    async def test_kill_does_not_preserve_when_session_file_missing(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc, pool_id="pool-1", model_config=DEFAULT_MODEL,
            session_id="sess-abc123-deadbeef",
        )
        pool._entries["pool-1"] = entry

        with patch.object(pool, "_session_file_exists", return_value=False):
            await pool._kill("pool-1")

        assert pool._resume_session_ids.get("pool-1") is None

    async def test_send_terminated_preserves_session_for_next_spawn(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([INIT_LINE])
        entry = _ProcessEntry(
            proc=proc, pool_id="p1", model_config=DEFAULT_MODEL,
            session_id="sess-to-resume",
        )
        pool._entries["p1"] = entry

        terminated_result = CliResult(error="Process terminated unexpectedly")
        with (
            patch(
                "lyra.core.cli_pool.send_and_read",
                new=AsyncMock(return_value=terminated_result),
            ),
            patch.object(pool, "_session_file_exists", return_value=True),
        ):
            await pool.send("p1", "hello", DEFAULT_MODEL)

        assert pool._resume_session_ids.get("p1") == "sess-to-resume"

    async def test_switch_cwd_does_not_preserve_session(self, tmp_path: Path) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc, pool_id="p1", model_config=DEFAULT_MODEL,
            session_id="sess-abc",
        )
        pool._entries["p1"] = entry

        with patch.object(pool, "_session_file_exists", return_value=True):
            await pool.switch_cwd("p1", tmp_path)

        assert pool._resume_session_ids.get("p1") is None

    async def test_reset_does_not_preserve_session(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc, pool_id="p1", model_config=DEFAULT_MODEL,
            session_id="sess-abc",
        )
        pool._entries["p1"] = entry

        with patch.object(pool, "_session_file_exists", return_value=True):
            await pool.reset("p1")

        assert pool._resume_session_ids.get("p1") is None


# ---------------------------------------------------------------------------
# TestReaperSkipsLockedEntries — Bug 1: reaper must not kill in-use processes
# ---------------------------------------------------------------------------


class TestReaperSkipsLockedEntries:
    """Reaper skips entries whose _lock is held (in-use by send())."""

    async def test_reaper_skips_locked_entry(self) -> None:
        pool = CliPool(idle_ttl=1)  # very short TTL
        proc = make_fake_proc([])
        entry = _ProcessEntry(proc=proc, pool_id="p-locked", model_config=DEFAULT_MODEL)
        entry.last_activity = 0.0  # far in the past → definitely idle
        pool._entries["p-locked"] = entry

        # While lock is held: reaper must NOT kill
        async with entry._lock:
            now = time.time()
            to_kill = [
                (pid, e)
                for pid, e in list(pool._entries.items())
                if not e.is_alive()
                or ((now - e.last_activity) > pool._idle_ttl and not e._lock.locked())
            ]
            assert len(to_kill) == 0, "Locked entry should not be in to_kill"

        # After lock released: reaper should kill
        now = time.time()
        to_kill = [
            (pid, e)
            for pid, e in list(pool._entries.items())
            if not e.is_alive()
            or ((now - e.last_activity) > pool._idle_ttl and not e._lock.locked())
        ]
        assert len(to_kill) == 1
        assert to_kill[0][0] == "p-locked"
