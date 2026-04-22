"""Tests for CliPool state management: cwd, resume/reset, switch_cwd, eager cleanup."""

from __future__ import annotations

import time
from pathlib import Path
from unittest.mock import AsyncMock, patch

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.cli.cli_pool import CliPool, _ProcessEntry
from lyra.core.cli.cli_protocol import CliResult

from .conftest_cli_pool import (
    _PATCH_TARGET,
    ASSISTANT_LINE,
    DEFAULT_MODEL,
    INIT_LINE,
    RESULT_LINE,
    make_fake_proc,
)

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
        from lyra.core.cli.cli_pool import _LYRA_ROOT

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
        """After resume_and_reset(), CLI session stored and process killed."""
        pool = CliPool()
        proc = make_fake_proc([])
        # Pre-populate a live entry so _kill has something to terminate
        entry = _ProcessEntry(
            proc=proc, pool_id="pool:tg:chat:1", model_config=DEFAULT_MODEL
        )
        pool._entries["pool:tg:chat:1"] = entry

        _CLI_SESS = "abcdef01-2345-6789-abcd-ef0123456789"
        _LYRA_SESS = "11111111-2222-3333-4444-555555555555"

        # Wire a mock TurnStore — simulates a prior interaction that persisted
        # the real CLI session ID in pool_sessions.
        from unittest.mock import AsyncMock

        from lyra.infrastructure.stores.turn_store import TurnStore

        mock_store = AsyncMock(spec=TurnStore)
        mock_store.get_cli_session = AsyncMock(return_value=_CLI_SESS)
        pool.set_turn_store(mock_store)

        # Act — pipeline passes the Lyra session; resume_and_reset looks up
        # the CLI session from TurnStore.
        await pool.resume_and_reset("pool:tg:chat:1", _LYRA_SESS)

        # Assert — CLI session stored for next spawn AND process killed
        assert pool._resume_session_ids.get("pool:tg:chat:1") == _CLI_SESS
        assert "pool:tg:chat:1" not in pool._entries

    async def test_spawn_consumes_resume_session_id_and_passes_to_cmd(self) -> None:
        """_spawn() pops _resume_session_ids and passes --resume to CLI (one-shot)."""
        pool = CliPool()
        pool._resume_session_ids["pool:resume:1"] = "sess-abc"
        proc = make_fake_proc([INIT_LINE, ASSISTANT_LINE, RESULT_LINE])

        # Act — patch subprocess so no real process is started
        with patch(_PATCH_TARGET, new=AsyncMock(return_value=proc)) as mock_spawn:
            await pool.send("pool:resume:1", "hello", DEFAULT_MODEL)

        # Assert — --resume flag present in spawned command
        cmd_args = list(mock_spawn.call_args[0])
        assert "--resume" in cmd_args
        assert cmd_args[cmd_args.index("--resume") + 1] == "sess-abc"
        # Assert — one-shot: intent consumed after spawn
        assert pool._resume_session_ids.get("pool:resume:1") is None


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

        from lyra.core.cli.cli_protocol import CliResult

        terminated_result = CliResult(error="Process terminated unexpectedly")
        with patch(
            "lyra.core.cli.cli_pool.send_and_read",
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

        from lyra.core.cli.cli_protocol import CliResult

        with patch(
            "lyra.core.cli.cli_pool.send_and_read",
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

    async def test_kill_preserves_session(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="pool-1",
            model_config=DEFAULT_MODEL,
            session_id="sess-abc123-deadbeef",
        )
        pool._entries["pool-1"] = entry

        await pool._kill("pool-1")

        assert pool._resume_session_ids.get("pool-1") == "sess-abc123-deadbeef"

    async def test_kill_does_not_preserve_when_preserve_false(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="pool-1",
            model_config=DEFAULT_MODEL,
            session_id="sess-abc123-deadbeef",
        )
        pool._entries["pool-1"] = entry

        await pool._kill("pool-1", preserve_session=False)

        assert pool._resume_session_ids.get("pool-1") is None

    async def test_kill_does_not_preserve_when_session_id_is_none(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="pool-1",
            model_config=DEFAULT_MODEL,
            session_id=None,
        )
        pool._entries["pool-1"] = entry

        await pool._kill("pool-1")

        assert pool._resume_session_ids.get("pool-1") is None

    async def test_send_terminated_preserves_session_for_next_spawn(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([INIT_LINE])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="p1",
            model_config=DEFAULT_MODEL,
            session_id="sess-to-resume",
        )
        pool._entries["p1"] = entry

        terminated_result = CliResult(error="Process terminated unexpectedly")
        with patch(
            "lyra.core.cli.cli_pool.send_and_read",
            new=AsyncMock(return_value=terminated_result),
        ):
            await pool.send("p1", "hello", DEFAULT_MODEL)

        assert pool._resume_session_ids.get("p1") == "sess-to-resume"

    async def test_switch_cwd_does_not_preserve_session(self, tmp_path: Path) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="p1",
            model_config=DEFAULT_MODEL,
            session_id="sess-abc",
        )
        pool._entries["p1"] = entry

        await pool.switch_cwd("p1", tmp_path)

        assert pool._resume_session_ids.get("p1") is None

    async def test_reset_does_not_preserve_session(self) -> None:
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="p1",
            model_config=DEFAULT_MODEL,
            session_id="sess-abc",
        )
        pool._entries["p1"] = entry

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


# ---------------------------------------------------------------------------
# TestSyncEvictEntry — #370: TTL eviction must preserve session for auto-resume
# ---------------------------------------------------------------------------


class TestSyncEvictEntry:
    """_sync_evict_entry() pops entry + cwd_override; preserves session_id iff conditions hold."""  # noqa: E501

    def test_sync_evict_entry_preserves_session(self) -> None:
        """preserve_session=True + session_id set → _resume_session_ids populated."""
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="pool-1",
            model_config=DEFAULT_MODEL,
            session_id="sess-abc123-deadbeef",
        )
        pool._entries["pool-1"] = entry
        pool._cwd_overrides["pool-1"] = Path("/tmp/cwd")

        pool._sync_evict_entry("pool-1", preserve_session=True)

        assert pool._resume_session_ids.get("pool-1") == "sess-abc123-deadbeef"
        assert "pool-1" not in pool._entries
        assert "pool-1" not in pool._cwd_overrides

    def test_sync_evict_entry_no_preserve_when_preserve_false(self) -> None:
        """preserve_session=False → _resume_session_ids not written."""
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="pool-1",
            model_config=DEFAULT_MODEL,
            session_id="sess-abc123-deadbeef",
        )
        pool._entries["pool-1"] = entry
        pool._cwd_overrides["pool-1"] = Path("/tmp/cwd")

        pool._sync_evict_entry("pool-1", preserve_session=False)

        assert pool._resume_session_ids.get("pool-1") is None
        assert "pool-1" not in pool._entries
        assert "pool-1" not in pool._cwd_overrides

    def test_sync_evict_entry_no_preserve_when_session_id_none(self) -> None:
        """session_id=None → _resume_session_ids not written."""
        pool = CliPool()
        proc = make_fake_proc([])
        entry = _ProcessEntry(
            proc=proc,
            pool_id="pool-1",
            model_config=DEFAULT_MODEL,
            session_id=None,
        )
        pool._entries["pool-1"] = entry
        pool._cwd_overrides["pool-1"] = Path("/tmp/cwd")

        pool._sync_evict_entry("pool-1")

        assert pool._resume_session_ids.get("pool-1") is None
        assert "pool-1" not in pool._entries
        assert "pool-1" not in pool._cwd_overrides

    def test_sync_evict_entry_no_op_when_entry_absent(self) -> None:
        """pool_id not in _entries → silent no-op, no KeyError."""
        pool = CliPool()
        pool._sync_evict_entry("nonexistent")  # must not raise
        assert pool._resume_session_ids.get("nonexistent") is None
