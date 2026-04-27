"""Non-streaming protocol layer for the Claude CLI subprocess.

Extracted from cli_protocol.py — pure non-streaming I/O protocol concerns:
send_and_read and read_until_result for batch-style request/response.
"""

from __future__ import annotations

import asyncio
import json
import logging

from .cli_pool_entry import _ProcessEntry
from .cli_protocol_types import CliProtocolOptions, CliResult, _read_stderr_snippet

log = logging.getLogger(__name__)


async def send_and_read(  # noqa: PLR0913 — protocol fn: positional args map 1:1 to wire-level concerns
    entry: _ProcessEntry,
    message: str,
    pool_id: str,
    *,
    default_timeout: float = 300,
    opts: CliProtocolOptions = CliProtocolOptions(),
) -> CliResult:
    """Write *message* to *entry*'s stdin as NDJSON, then read until a result.

    ``default_timeout`` is retried up to ``opts.max_idle_retries`` times.
    """
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
    entry: _ProcessEntry,
    *,
    pool_id: str,
    default_timeout: float = 300,
    opts: CliProtocolOptions = CliProtocolOptions(),
) -> CliResult:
    """Read stdout lines from *entry*'s process until a ``result`` event arrives.

    Raises no exceptions — returns ``CliResult(error=...)`` on failure.
    """
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
