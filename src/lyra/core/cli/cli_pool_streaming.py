"""Streaming mixin for CliPool — split from cli_pool.py (#760).

Provides send_streaming() and stale-resume logic for streaming turns.
"""

from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, cast

from ..agent.agent_config import ModelConfig
from .cli_pool_entry import _ProcessEntry
from .cli_pool_types import _CliPoolCore
from .cli_protocol import StreamingIterator, send_and_read_stream

if TYPE_CHECKING:
    from .cli_protocol import CliProtocolOptions

log = logging.getLogger(__name__)


class CliPoolStreamingMixin:
    """Mixin providing send_streaming() for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _entries: dict[str, _ProcessEntry]
        _default_timeout: int
        _protocol_opts: CliProtocolOptions

    # How long to wait after a resumed spawn to detect a stale session.
    # The CLI exits within ~1ms when a session doesn't exist; 50ms gives
    # the asyncio child watcher plenty of time to set proc.returncode.
    _STALE_RESUME_CHECK_DELAY = 0.05

    async def send_streaming(  # noqa: C901
        self,
        pool_id: str,
        message: str,
        model_config: ModelConfig,
        system_prompt: str = "",
    ) -> StreamingIterator:
        """Send a message and return a streaming iterator for text_delta chunks.

        Locking model: acquire entry._lock → write stdin → release lock →
        return iterator.  The lock is released before the first chunk is
        yielded so that concurrent reset() calls do not deadlock.
        """
        for _attempt in range(2):  # at most one stale-resume retry
            entry = self._entries.get(pool_id)

            _core = cast(_CliPoolCore, self)
            if entry is None or not entry.is_alive():
                entry = await _core._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    raise RuntimeError("Failed to spawn Claude CLI process")
            elif entry.system_prompt != system_prompt:
                log.info(
                    "[pool:%s] system_prompt changed — respawning (streaming)",
                    pool_id,
                )
                await _core._kill(pool_id, preserve_session=False)
                entry = await _core._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    raise RuntimeError("Failed to respawn Claude CLI process")
            elif entry.model_config != model_config:
                log.info(
                    "[pool:%s] model_config mismatch — respawning (streaming)",
                    pool_id,
                )
                await _core._kill(pool_id, preserve_session=False)
                entry = await _core._spawn(pool_id, model_config, system_prompt)
                if entry is None:
                    raise RuntimeError("Failed to respawn Claude CLI process")

            async def _reset() -> None:
                if self._entries.get(pool_id) is entry:
                    await cast(_CliPoolCore, self).reset(pool_id)

            # Lock: write stdin inside lock, release before returning the
            # read-only iterator.  This prevents concurrent stdin interleave
            # while allowing the iterator to be consumed without holding the lock.
            async with entry._lock:
                if not entry.is_alive():
                    raise RuntimeError("Process died before streaming send")
                entry.turn_count += 1
                entry.last_activity = time.time()
                iterator = await send_and_read_stream(
                    entry,
                    message,
                    pool_id,
                    pool_reset_fn=_reset,
                    default_timeout=self._default_timeout,
                    opts=self._protocol_opts,
                )

            # Stale resume guard: if this process was spawned with --resume,
            # briefly yield to let the event loop process a potential child-exit
            # signal.  The CLI exits in ~1ms when the session doesn't exist.
            # post-increment: bumped inside lock before this check
            if _attempt == 0 and entry.resumed_from and entry.turn_count == 1:
                await asyncio.sleep(self._STALE_RESUME_CHECK_DELAY)
                if not entry.is_alive():
                    log.warning(
                        "[pool:%s] stale resume (session %s) — retrying"
                        " without --resume (streaming)",
                        pool_id,
                        entry.resumed_from,
                    )
                    await cast(_CliPoolCore, self)._kill(
                        pool_id, preserve_session=False
                    )
                    continue

            return iterator

        raise RuntimeError("Failed after stale resume retry")
