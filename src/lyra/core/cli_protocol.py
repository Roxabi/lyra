"""NDJSON protocol layer for the Claude CLI subprocess.

Pure I/O protocol concerns: payload serialisation, event parsing, timeout
handling, command construction, and the result state machine.  No pool
state is held here — all pool-specific context is passed as explicit
arguments.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import tempfile
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from .agent_config import ModelConfig
from .events import LlmEvent, ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Subprocess helpers
# ---------------------------------------------------------------------------


async def _read_stderr_snippet(
    proc: asyncio.subprocess.Process, limit: int = 512
) -> str:
    """Read up to *limit* bytes from proc.stderr without blocking.

    Returns a stripped string, or "" if nothing is available.
    """
    if proc.stderr is None:
        return ""
    try:
        raw = await asyncio.wait_for(proc.stderr.read(limit), timeout=0.5)
        return raw.decode(errors="replace").strip()
    except (asyncio.TimeoutError, Exception):
        return ""


def build_cmd(
    model_config: ModelConfig,
    session_id: str | None = None,
    system_prompt: str = "",
) -> tuple[list[str], str | None]:
    """Build the ``claude`` CLI command list from *model_config*.

    Returns ``(cmd, prompt_file)`` where *prompt_file* is a temp path
    that the caller must clean up, or ``None`` when no system prompt
    was written.
    """
    cmd = [
        "claude",
        "--input-format",
        "stream-json",
        "--output-format",
        "stream-json",
        "--verbose",
        "--model",
        model_config.model,
        # max_turns=None → unlimited; 0 is DB sentinel for None.
        *(
            ["--max-turns", str(model_config.max_turns)]
            if model_config.max_turns
            else []
        ),
    ]
    if model_config.streaming:
        cmd.append("--include-partial-messages")
    if model_config.skip_permissions:
        cmd.append("--dangerously-skip-permissions")
    if model_config.tools:
        cmd.extend(["--allowedTools", ",".join(model_config.tools)])
    prompt_file: str | None = None
    if system_prompt:
        fd, prompt_file = tempfile.mkstemp(suffix=".txt", prefix="lyra-prompt-")
        try:
            os.write(fd, system_prompt.encode("utf-8"))
        finally:
            os.close(fd)
        os.chmod(prompt_file, 0o600)
        cmd.extend(["--system-prompt-file", prompt_file])
    if session_id:
        cmd.extend(["--resume", session_id])
    return cmd, prompt_file


# Validate Claude session IDs (strict UUID format).
SESSION_ID_RE = re.compile(
    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
)
# Private alias kept for backward compat (cli_pool.py imports this name).
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


@dataclass
class CliProtocolOptions:
    """Wire-level timing and retry options for the NDJSON protocol.

    Carries the three ``[cli_pool]`` knobs from ``config.toml`` into the
    protocol layer.  Defaults mirror pre-#369 hardcoded values.
    """

    stdin_drain_timeout: float = 10.0
    max_idle_retries: int = 3
    intermediate_timeout: float = 5.0


async def send_and_read(  # noqa: PLR0913 — protocol fn: positional args map 1:1 to wire-level concerns
    entry: object,  # _ProcessEntry — typed as object to avoid a circular import
    message: str,
    pool_id: str,
    *,
    default_timeout: float = 300,
    opts: CliProtocolOptions = CliProtocolOptions(),
) -> CliResult:
    """Write *message* to *entry*'s stdin as NDJSON, then read until a result.

    ``default_timeout`` is retried up to ``opts.max_idle_retries`` times.
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
        await asyncio.wait_for(proc.stdin.drain(), timeout=opts.stdin_drain_timeout)
    except asyncio.TimeoutError:
        return CliResult(error="Timeout writing to subprocess stdin")

    return await read_until_result(
        entry,
        pool_id=pool_id,
        default_timeout=default_timeout,
        opts=opts,
    )


async def read_until_result(  # noqa: C901, PLR0915
    entry: object,
    *,
    pool_id: str,
    default_timeout: float = 300,
    opts: CliProtocolOptions = CliProtocolOptions(),
) -> CliResult:
    """Read stdout lines from *entry*'s process until a ``result`` event arrives.

    Raises no exceptions — returns ``CliResult(error=...)`` on failure.
    """
    from .cli_pool import _ProcessEntry  # local import to avoid circularity

    assert isinstance(entry, _ProcessEntry)
    proc = entry.proc
    if proc.stdout is None:
        return CliResult(error="Process stdout is None")

    idle_timeout = default_timeout
    idle_retries = 0
    session_id: str | None = None
    result_parts: list[str] = []  # accumulate text across all assistant events

    try:
        while True:
            try:
                raw = await asyncio.wait_for(
                    proc.stdout.readline(), timeout=idle_timeout
                )
            except asyncio.TimeoutError:
                # No output for idle_timeout seconds — process may be stuck.
                if not entry.is_alive():
                    stderr_hint = await _read_stderr_snippet(proc)
                    detail = f": {stderr_hint}" if stderr_hint else ""
                    log.warning(
                        "[pool:%s] process died during idle wait (rc=%s)%s",
                        pool_id,
                        proc.returncode,
                        detail,
                    )
                    return CliResult(
                        error=(
                            f"Process terminated unexpectedly"
                            f" (rc={proc.returncode}){detail}"
                        ),
                    )
                idle_retries += 1
                if idle_retries >= opts.max_idle_retries:
                    total_wait = idle_timeout * opts.max_idle_retries
                    log.error(
                        "[pool:%s] Timeout: no output for %ds (%d retries)",
                        pool_id,
                        total_wait,
                        opts.max_idle_retries,
                    )
                    return CliResult(error=f"Timeout: no output for {total_wait}s")
                log.warning(
                    "[pool:%s] no output for %ds — alive, waiting (%d/%d)",
                    pool_id,
                    idle_timeout,
                    idle_retries,
                    opts.max_idle_retries,
                )
                continue

            if not raw:
                stderr_hint = await _read_stderr_snippet(proc)
                detail = f": {stderr_hint}" if stderr_hint else ""
                log.warning(
                    "[pool:%s] stdout EOF (process died, rc=%s)%s",
                    pool_id,
                    proc.returncode,
                    detail,
                )
                return CliResult(
                    error=(
                        f"Process terminated unexpectedly"
                        f" (rc={proc.returncode}){detail}"
                    ),
                )

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

            if msg_type == "result":
                if not session_id:
                    session_id = data.get("session_id", "")
                if session_id:
                    entry.update_session_id(session_id)
                result_text = data.get("result") or "\n\n".join(result_parts)
                # CLI puts detailed errors in errors[] (e.g. "No conversation found")
                errors = data.get("errors", [])
                error_detail = errors[0] if errors else ""
                log.info(
                    "[pool:%s] response: %d chars, %dms",
                    pool_id,
                    len(result_text),
                    data.get("duration_ms", 0),
                )

                if data.get("is_error", False):
                    subtype = data.get("subtype", "")
                    if subtype == "success" and result_text:
                        # CLI returns is_error=True + subtype="success"
                        # when a tool call failed but the model recovered.
                        log.info(
                            "[pool:%s] is_error=True but subtype=success"
                            " — treating as success",
                            pool_id,
                        )
                        return CliResult(
                            result=result_text,
                            session_id=session_id or "",
                        )
                    if subtype == "error_max_turns" and result_text:
                        return CliResult(
                            result=result_text,
                            session_id=session_id or "",
                            warning="Response truncated (max turns reached)",
                        )
                    return CliResult(
                        error=(
                            error_detail or result_text or "Unknown error from Claude"
                        ),
                    )

                return CliResult(result=result_text, session_id=session_id or "")

    except Exception as exc:
        log.exception("[pool:%s] read error: %s", pool_id, exc)
        return CliResult(error=f"Read error: {type(exc).__name__}")


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
