"""Streaming protocol layer for the Claude CLI subprocess.

Extracted from cli_protocol.py — pure streaming I/O protocol concerns:
StreamingIterator for text_delta chunks, and send_and_read_stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Awaitable, Callable

from ..messaging.events import LlmEvent
from .cli_protocol_types import CliProtocolOptions, _read_stderr_snippet
from .cli_streaming_parser import CliStreamingParser

log = logging.getLogger(__name__)

# Re-export for API preservation
__all__ = ["StreamingIterator", "send_and_read_stream", "CliStreamingParser"]


class StreamingIterator:
    """AsyncIterator for stream_event text_delta chunks from a CLI subprocess.

    Exposes ``session_id`` for ``pool_processor``.  ``aclose()`` calls
    *pool_reset_fn* to kill the subprocess on cancellation.
    """

    def __init__(  # noqa: PLR0913 — protocol fn: positional args map 1:1 to wire-level concerns
        self,
        entry: object,
        pool_id: str,
        *,
        pool_reset_fn: Callable[[], Awaitable[None]] | None = None,
        default_timeout: float = 300,
        opts: CliProtocolOptions = CliProtocolOptions(),
    ) -> None:
        from .cli_pool import _ProcessEntry

        assert isinstance(entry, _ProcessEntry)
        self._entry = entry
        self._pool_id = pool_id
        self._pool_reset_fn = pool_reset_fn
        self._default_timeout = default_timeout
        self._max_idle_retries = opts.max_idle_retries
        self._idle_retries = 0
        self._parser = CliStreamingParser(pool_id)
        self._done = False  # I/O-level done flag (EOF, timeout, cleanup)

    @property
    def session_id(self) -> str | None:
        """Session ID from parser state."""
        return self._parser.session_id

    @session_id.setter
    def session_id(self, value: str | None) -> None:
        self._parser.session_id = value

    @property
    def error(self) -> str | None:
        """Error from parser state."""
        return self._parser.error

    @error.setter
    def error(self, value: str | None) -> None:
        self._parser.error = value

    def __aiter__(self) -> "StreamingIterator":
        return self

    async def __anext__(self) -> LlmEvent:  # noqa: C901 — protocol event dispatch with I/O
        if self._done or self._parser._done:
            self._done = True
            raise StopAsyncIteration

        pending = self._parser._pending
        if pending:
            return pending.popleft()

        entry = self._entry
        assert entry is not None
        proc = entry.proc
        if proc.stdout is None:
            self._done = True
            raise StopAsyncIteration

        while True:
            if pending:
                return pending.popleft()

            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=self._default_timeout
                )
            except asyncio.TimeoutError:
                if not entry.is_alive():
                    stderr_hint = await _read_stderr_snippet(proc)
                    detail = f": {stderr_hint}" if stderr_hint else ""
                    log.warning(
                        "[pool:%s] process died during streaming idle wait (rc=%s)%s",
                        self._pool_id,
                        proc.returncode,
                        detail,
                    )
                    self._parser.error = (
                        f"Process terminated unexpectedly"
                        f" (rc={proc.returncode}){detail}"
                    )
                    self._done = True
                    raise StopAsyncIteration
                self._idle_retries += 1
                if self._idle_retries >= self._max_idle_retries:
                    log.error(
                        "[pool:%s] streaming timeout: no output for %ds",
                        self._pool_id,
                        self._default_timeout * self._max_idle_retries,
                    )
                    # Kill the alive-but-unresponsive subprocess before
                    # marking done (otherwise aclose() skips cleanup).
                    await self._cleanup()
                    raise StopAsyncIteration
                continue

            if not raw:
                stderr_hint = await _read_stderr_snippet(proc)
                detail = f": {stderr_hint}" if stderr_hint else ""
                log.warning(
                    "[pool:%s] stdout EOF during streaming (rc=%s)%s",
                    self._pool_id,
                    proc.returncode,
                    detail,
                )
                self._parser.error = (
                    f"Process terminated unexpectedly (rc={proc.returncode}){detail}"
                )
                self._done = True
                raise StopAsyncIteration

            self._idle_retries = 0
            line = raw.decode().strip()
            if not line:
                continue

            # Delegate parsing to CliStreamingParser
            self._parser.parse_line(line)

            # Sync session_id back to entry (for --resume on next turn)
            if self._parser.session_id:
                entry.update_session_id(self._parser.session_id)

            # Check if parser set done (result event)
            if self._parser._done:
                self._done = True

            if pending:
                return pending.popleft()

    async def _cleanup(self) -> None:
        """Call pool_reset_fn and mark done. Safe to call multiple times."""
        if not self._done and self._pool_reset_fn is not None:
            try:
                await self._pool_reset_fn()
            except Exception:
                log.warning(
                    "[pool:%s] pool_reset_fn failed in streaming cleanup",
                    self._pool_id,
                    exc_info=True,
                )
        self._done = True

    async def aclose(self) -> None:
        """Clean up: kill subprocess if the stream was not fully consumed."""
        await self._cleanup()


async def send_and_read_stream(  # noqa: PLR0913 -- protocol fn: positional args map 1:1 to wire-level concerns
    entry: object,
    message: str,
    pool_id: str,
    *,
    pool_reset_fn: Callable[[], Awaitable[None]] | None = None,
    default_timeout: float = 300,
    opts: CliProtocolOptions = CliProtocolOptions(),
) -> StreamingIterator:
    """Write *message* to stdin and return a StreamingIterator[LlmEvent].

    The iterator exposes ``session_id`` (None until parsed from the stream).
    Call ``aclose()`` to kill the subprocess on cancellation.
    """
    from .cli_pool import _ProcessEntry

    assert isinstance(entry, _ProcessEntry)
    proc = entry.proc
    if proc.stdin is None:
        it = StreamingIterator(
            entry,
            pool_id,
            opts=opts,
        )
        it._done = True
        return it

    payload = {
        "type": "user",
        "message": {"role": "user", "content": message},
        # always empty — session binding is handled by --resume at spawn
        "session_id": "",
        "parent_tool_use_id": None,
    }
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    try:
        await asyncio.wait_for(proc.stdin.drain(), timeout=opts.stdin_drain_timeout)
    except asyncio.TimeoutError:
        log.error("[pool:%s] timeout writing stdin (streaming)", pool_id)
        it = StreamingIterator(
            entry,
            pool_id,
            pool_reset_fn=pool_reset_fn,
            opts=opts,
        )
        await it._cleanup()
        return it

    return StreamingIterator(
        entry,
        pool_id,
        pool_reset_fn=pool_reset_fn,
        default_timeout=default_timeout,
        opts=opts,
    )
