"""Streaming protocol layer for the Claude CLI subprocess.

Extracted from cli_protocol.py — pure streaming I/O protocol concerns:
StreamingIterator for text_delta chunks, and send_and_read_stream.
"""

from __future__ import annotations

import asyncio
import json
import logging
from collections import deque
from collections.abc import Awaitable, Callable

from .cli_protocol import CliProtocolOptions, _read_stderr_snippet
from .events import LlmEvent, ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

log = logging.getLogger(__name__)


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
        self._done = False
        self._had_text_delta = False
        self._pending: deque[LlmEvent] = deque()
        self.session_id: str | None = None
        self.error: str | None = None

    def __aiter__(self) -> "StreamingIterator":
        return self

    async def __anext__(self) -> LlmEvent:  # noqa: C901, PLR0912, PLR0915 — protocol event dispatch
        if self._done:
            raise StopAsyncIteration

        if self._pending:
            return self._pending.popleft()

        entry = self._entry
        assert entry is not None
        proc = entry.proc
        if proc.stdout is None:
            self._done = True
            raise StopAsyncIteration

        while True:
            if self._pending:
                return self._pending.popleft()

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
                    self.error = (
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
                self.error = (
                    f"Process terminated unexpectedly (rc={proc.returncode}){detail}"
                )
                self._done = True
                raise StopAsyncIteration

            self._idle_retries = 0
            line = raw.decode().strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                continue

            msg_type = data.get("type", "")

            if msg_type == "system" and data.get("subtype") == "init":
                self.session_id = data.get("session_id", "") or None
                if self.session_id:
                    entry.update_session_id(self.session_id)
                log.debug(
                    "[pool:%s] streaming init: session=%s",
                    self._pool_id,
                    self.session_id,
                )

            elif msg_type == "assistant":
                blocks = data.get("message", {}).get("content", [])
                for b in blocks:
                    if b.get("type") == "tool_use":
                        self._pending.append(
                            ToolUseLlmEvent(
                                tool_name=b.get("name", ""),
                                tool_id=b.get("id", ""),
                                input=b.get("input", {}),
                            )
                        )
                if self._pending:
                    return self._pending.popleft()

            elif msg_type == "stream_event":
                event_data = data.get("event", data)
                event_type = event_data.get("type", "")
                if event_type == "content_block_start":
                    cb = event_data.get("content_block", {})
                    if cb.get("type") == "tool_use":
                        self._pending.append(
                            ToolUseLlmEvent(
                                tool_name=cb.get("name", ""),
                                tool_id=cb.get("id", ""),
                                input={},
                            )
                        )
                        if self._pending:
                            return self._pending.popleft()
                elif event_type == "content_block_delta":
                    delta = event_data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            self._had_text_delta = True
                            return TextLlmEvent(text=text)

            elif msg_type == "result":
                sid = data.get("session_id", "")
                if sid:
                    self.session_id = sid
                    entry.update_session_id(sid)
                is_error = data.get("is_error", False)
                subtype = data.get("subtype", "")
                # Classify based on observed stream, not self-reported flags.
                # CLI reports is_error=True + subtype="success" both when:
                #   (a) a tool call failed but the model recovered and
                #       streamed a valid answer via text_delta events, and
                #   (b) the CLI exited early (auth failure, crash) and
                #       emitted the error text only in the result field.
                # Only (a) should downgrade to success — and the signal for
                # that is whether any text was actually streamed.
                if is_error and subtype == "success" and self._had_text_delta:
                    log.info(
                        "[pool:%s] streaming result is_error=True but"
                        " subtype=success with streamed text — treating"
                        " as success",
                        self._pool_id,
                    )
                    is_error = False
                elif is_error:
                    errors = data.get("errors", [])
                    self.error = (
                        errors[0]
                        if errors
                        else data.get("result") or subtype or "Unknown streaming error"
                    )
                    log.warning(
                        "[pool:%s] streaming result is_error=True"
                        " subtype=%s had_text_delta=%s duration_ms=%d"
                        " result=%r",
                        self._pool_id,
                        subtype,
                        self._had_text_delta,
                        data.get("duration_ms", 0),
                        (data.get("result") or "")[:200],
                    )
                log.info(
                    "[pool:%s] streaming result: %dms",
                    self._pool_id,
                    data.get("duration_ms", 0),
                )
                self._done = True
                return ResultLlmEvent(
                    is_error=is_error,
                    duration_ms=data.get("duration_ms", 0),
                    cost_usd=None,
                    error_text=self.error if is_error else None,
                )

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


async def send_and_read_stream(  # noqa: PLR0913 — protocol fn: positional args map 1:1 to wire-level concerns
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
