"""NDJSON protocol layer for the Claude CLI subprocess.

Pure I/O protocol concerns: payload serialisation, event parsing, timeout
handling, and the result state machine.  No pool state is held here — all
pool-specific context is passed as explicit arguments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

# Regex used to validate Claude session IDs (hex-and-dash, 8–64 chars).
SESSION_ID_RE = re.compile(r"^[0-9a-f-]{8,64}$")

# Keep the private alias so cli_pool.py can import it under its old name.
_SESSION_ID_RE = SESSION_ID_RE


@dataclass
class CliResult:
    """Result from a CliPool.send() call.

    Exactly one of ``result`` or ``error`` will be set (not both).
    ``warning`` is set when the response was truncated (e.g. max_turns reached).
    ``session_id`` is always present on success.
    """

    result: str = ""
    session_id: str = ""
    error: str = ""
    warning: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


async def send_and_read(
    entry: object,  # _ProcessEntry — typed as object to avoid a circular import
    message: str,
    pool_id: str,
    *,
    on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    default_timeout: float = 300,
) -> CliResult:
    """Write *message* to *entry*'s stdin as NDJSON, then read until a result.

    Parameters
    ----------
    entry:
        A ``_ProcessEntry``-like object with ``proc``, ``session_id``, and
        ``is_alive()`` accessible as attributes.
    message:
        The user message text to send.
    pool_id:
        Used only for log messages (no pool state is read/written here).
    on_intermediate:
        Optional async callback invoked for every assistant turn except the
        last, allowing callers to stream partial responses.
    default_timeout:
        Per-readline idle timeout in seconds.  The protocol retries up to
        ``max_idle_retries`` times before returning a Timeout error.
    """
    from .cli_pool import _ProcessEntry  # local import to avoid circularity

    assert isinstance(entry, _ProcessEntry)
    proc = entry.proc
    if proc.stdin is None:
        return CliResult(error="Process stdin is None")

    payload = {
        "type": "user",
        "message": {"role": "user", "content": message},
        # always empty — session binding is handled by --resume at spawn
        "session_id": "",
        "parent_tool_use_id": None,
    }
    proc.stdin.write((json.dumps(payload) + "\n").encode())
    try:
        await asyncio.wait_for(proc.stdin.drain(), timeout=10)
    except asyncio.TimeoutError:
        return CliResult(error="Timeout writing to subprocess stdin")

    return await read_until_result(
        entry,
        pool_id=pool_id,
        on_intermediate=on_intermediate,
        default_timeout=default_timeout,
    )


async def read_until_result(  # noqa: C901, PLR0915 — protocol dispatch: each JSON event type requires its own branch
    entry: object,
    *,
    pool_id: str,
    on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    default_timeout: float = 300,
) -> CliResult:
    """Read stdout lines from *entry*'s process until a ``result`` event arrives.

    Parameters
    ----------
    entry:
        A ``_ProcessEntry``-like object with ``proc``, ``session_id``, and
        ``is_alive()`` accessible as attributes.
    pool_id:
        Used only for log messages.
    on_intermediate:
        Optional async callback invoked for intermediate assistant turns.
    default_timeout:
        Per-readline idle timeout in seconds.
    """
    from .cli_pool import _ProcessEntry  # local import to avoid circularity

    assert isinstance(entry, _ProcessEntry)
    proc = entry.proc
    if proc.stdout is None:
        return CliResult(error="Process stdout is None")

    idle_timeout = default_timeout
    max_idle_retries = 3
    idle_retries = 0
    session_id: str | None = None
    result_parts: list[str] = []  # accumulate text across all assistant events
    assistant_turn_count = 0
    # Buffer current turn; previous turn sent as ⏳ intermediate
    pending_intermediate: str | None = None

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=idle_timeout
                )
            except asyncio.TimeoutError:
                # No output for idle_timeout seconds — process may be stuck.
                if not entry.is_alive():
                    log.warning("[pool:%s] process died during idle wait", pool_id)
                    return CliResult(error="Process terminated unexpectedly")
                idle_retries += 1
                if idle_retries >= max_idle_retries:
                    total_wait = idle_timeout * max_idle_retries
                    log.error(
                        "[pool:%s] Timeout: no output for %ds (%d retries)",
                        pool_id,
                        total_wait,
                        max_idle_retries,
                    )
                    return CliResult(error=f"Timeout: no output for {total_wait}s")
                log.warning(
                    "[pool:%s] no output for %ds — alive, waiting (%d/%d)",
                    pool_id,
                    idle_timeout,
                    idle_retries,
                    max_idle_retries,
                )
                continue

            if not raw:
                log.warning("[pool:%s] stdout EOF (process died)", pool_id)
                return CliResult(error="Process terminated unexpectedly")

            idle_retries = 0  # got data — reset timeout counter
            line = raw.decode().strip()
            if not line:
                continue

            try:
                data = json.loads(line)
            except json.JSONDecodeError:
                log.debug("[pool:%s] non-JSON: %s", pool_id, line[:100])
                continue

            msg_type = data.get("type", "")

            if msg_type == "system" and data.get("subtype") == "init":
                session_id = data.get("session_id", "")
                model = data.get("model")
                log.debug("[pool:%s] init: model=%s", pool_id, model)
            if msg_type == "assistant":
                blocks = data.get("message", {}).get("content", [])
                texts = [b["text"] for b in blocks if b.get("type") == "text"]
                result_parts.extend(texts)
                assistant_turn_count += 1
                if on_intermediate and texts and assistant_turn_count >= 2:
                    # Flush the *previous* buffered turn as ⏳ before
                    # buffering the current one.
                    if pending_intermediate is not None:
                        try:
                            await asyncio.wait_for(
                                on_intermediate(pending_intermediate),
                                timeout=5.0,
                            )
                        except asyncio.TimeoutError:
                            log.warning(
                                "[pool:%s] on_intermediate callback timed"
                                " out (>5s) — dropping",
                                pool_id,
                            )
                        except Exception:
                            log.warning(
                                "[pool:%s] on_intermediate callback failed",
                                pool_id,
                                exc_info=True,
                            )
                    pending_intermediate = "\n\n".join(texts)

            if msg_type == "result":
                if not session_id:
                    session_id = data.get("session_id", "")
                if session_id and entry.session_id != session_id:
                    entry.session_id = session_id
                if pending_intermediate is not None:
                    result_text = pending_intermediate
                else:
                    result_text = data.get("result") or "\n\n".join(result_parts)
                log.info(
                    "[pool:%s] response: %d chars, %dms",
                    pool_id,
                    len(result_text),
                    data.get("duration_ms", 0),
                )

                if data.get("is_error", False):
                    subtype = data.get("subtype", "")
                    if subtype == "error_max_turns" and result_text:
                        return CliResult(
                            result=result_text,
                            session_id=session_id or "",
                            warning="Response truncated (max turns reached)",
                        )
                    return CliResult(error=result_text or "Unknown error from Claude")

                return CliResult(result=result_text, session_id=session_id or "")

    except Exception as exc:
        log.exception("[pool:%s] read error: %s", pool_id, exc)
        return CliResult(error=f"Read error: {type(exc).__name__}")


class StreamingIterator:
    """AsyncIterator wrapper that reads stream_event deltas from a CLI subprocess.

    Exposes a ``session_id`` attribute for ``pool_processor`` to read after
    the iterator is exhausted.  ``aclose()`` calls *pool_reset_fn* to kill the
    subprocess on cancellation (prevents pipe buffer deadlock).
    """

    def __init__(  # noqa: PLR0913
        self,
        entry: object,
        pool_id: str,
        *,
        pool_reset_fn: Callable[[], Awaitable[None]] | None = None,
        default_timeout: float = 300,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> None:
        from .cli_pool import _ProcessEntry

        assert isinstance(entry, _ProcessEntry)
        self._entry = entry
        self._pool_id = pool_id
        self._pool_reset_fn = pool_reset_fn
        self._default_timeout = default_timeout
        self._on_intermediate = on_intermediate
        self._idle_retries = 0
        self._done = False
        self.session_id: str | None = None
        self.error: str | None = None

    def __aiter__(self) -> "StreamingIterator":
        return self

    async def __anext__(self) -> str:  # noqa: C901, PLR0912, PLR0915 — protocol event dispatch
        if self._done:
            raise StopAsyncIteration

        entry = self._entry
        proc = entry.proc  # type: ignore[union-attr]
        if proc.stdout is None:
            self._done = True
            raise StopAsyncIteration

        while True:
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=self._default_timeout
                )
            except asyncio.TimeoutError:
                if not entry.is_alive():  # type: ignore[union-attr]
                    log.warning(
                        "[pool:%s] process died during streaming idle wait",
                        self._pool_id,
                    )
                    self._done = True
                    raise StopAsyncIteration
                self._idle_retries += 1
                if self._idle_retries >= 3:
                    log.error(
                        "[pool:%s] streaming timeout: no output for %ds",
                        self._pool_id,
                        self._default_timeout * 3,
                    )
                    # Kill the alive-but-unresponsive subprocess before
                    # marking done (otherwise aclose() skips cleanup).
                    await self._cleanup()
                    raise StopAsyncIteration
                continue

            if not raw:
                log.warning("[pool:%s] stdout EOF during streaming", self._pool_id)
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
                entry.session_id = self.session_id or entry.session_id  # type: ignore[union-attr]
                log.debug(
                    "[pool:%s] streaming init: session=%s",
                    self._pool_id,
                    self.session_id,
                )

            elif msg_type == "assistant":
                # Intermediate turns arrive before the final streamed turn.
                # Every assistant event in the streaming path is an intermediate
                # (tool use, sub-task reasoning, etc.) — fire on_intermediate for each.
                if self._on_intermediate is not None:
                    blocks = data.get("message", {}).get("content", [])
                    texts = [b["text"] for b in blocks if b.get("type") == "text"]
                    if texts:
                        try:
                            await asyncio.wait_for(
                                self._on_intermediate("\n\n".join(texts)),
                                timeout=5.0,
                            )
                        except asyncio.TimeoutError:
                            log.warning(
                                "[pool:%s] on_intermediate callback timed out"
                                " (>5s) — dropping",
                                self._pool_id,
                            )
                        except Exception:
                            log.warning(
                                "[pool:%s] on_intermediate callback failed",
                                self._pool_id,
                                exc_info=True,
                            )

            elif msg_type == "stream_event":
                event_data = data.get("event", data)
                event_type = event_data.get("type", "")
                if event_type == "content_block_delta":
                    delta = event_data.get("delta", {})
                    if delta.get("type") == "text_delta":
                        text = delta.get("text", "")
                        if text:
                            return text

            elif msg_type == "result":
                sid = data.get("session_id", "")
                if sid:
                    self.session_id = sid
                    if entry.session_id != sid:  # type: ignore[union-attr]
                        entry.session_id = sid  # type: ignore[union-attr]
                if data.get("is_error", False):
                    self.error = (
                        data.get("result")
                        or data.get("subtype")
                        or "Unknown streaming error"
                    )
                    log.warning(
                        "[pool:%s] streaming result is_error=True subtype=%s",
                        self._pool_id,
                        data.get("subtype", ""),
                    )
                log.info(
                    "[pool:%s] streaming result: %dms",
                    self._pool_id,
                    data.get("duration_ms", 0),
                )
                self._done = True
                raise StopAsyncIteration

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


async def send_and_read_stream(  # noqa: PLR0913
    entry: object,
    message: str,
    pool_id: str,
    *,
    pool_reset_fn: Callable[[], Awaitable[None]] | None = None,
    default_timeout: float = 300,
    on_intermediate: Callable[[str], Awaitable[None]] | None = None,
) -> StreamingIterator:
    """Write *message* to stdin then return a StreamingIterator for text_delta chunks.

    The returned iterator exposes a ``session_id`` attribute (``None`` until
    ``system/init`` or ``result`` is parsed).  Call ``aclose()`` to kill the
    subprocess on cancellation.

    Parameters
    ----------
    on_intermediate:
        Optional async callback fired for each intermediate assistant turn
        (tool use, sub-task reasoning) that precedes the final streaming turn.
    """
    from .cli_pool import _ProcessEntry

    assert isinstance(entry, _ProcessEntry)
    proc = entry.proc

    if proc.stdin is None:
        it = StreamingIterator(entry, pool_id, on_intermediate=on_intermediate)
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
        await asyncio.wait_for(proc.stdin.drain(), timeout=10)
    except asyncio.TimeoutError:
        log.error("[pool:%s] timeout writing stdin (streaming)", pool_id)
        it = StreamingIterator(
            entry, pool_id, pool_reset_fn=pool_reset_fn, on_intermediate=on_intermediate
        )
        await it._cleanup()
        return it

    return StreamingIterator(
        entry,
        pool_id,
        pool_reset_fn=pool_reset_fn,
        default_timeout=default_timeout,
        on_intermediate=on_intermediate,
    )
