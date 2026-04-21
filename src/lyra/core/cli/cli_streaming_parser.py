"""Pure JSON event parser for CLI streaming protocol.

Extracted from cli_streaming.py — parses NDJSON lines into LlmEvents
without any I/O or async concerns.
"""

from __future__ import annotations

import json
import logging
from collections import deque

from ..messaging.events import LlmEvent, ResultLlmEvent, TextLlmEvent, ToolUseLlmEvent

log = logging.getLogger(__name__)


class CliStreamingParser:
    """Pure JSON event parser for CLI streaming protocol.

    Parses NDJSON lines from the CLI subprocess stdout into LlmEvent objects.
    Maintains session state (session_id, error) across parse calls.
    """

    def __init__(self, pool_id: str) -> None:
        self.pool_id = pool_id
        self.session_id: str | None = None
        self.error: str | None = None
        self._had_text_delta = False
        self._done = False
        self._pending: deque[LlmEvent] = deque()

    def parse_line(self, line: str) -> deque[LlmEvent]:  # noqa: C901, PLR0912 — protocol event dispatch
        """Parse a JSON line, update state, and return events to yield.

        Returns a deque of LlmEvent objects. Caller should pop from left.
        Sets self._done = True when result event is parsed.
        """
        if self._done:
            return self._pending

        if not line:
            return self._pending

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            return self._pending

        msg_type = data.get("type", "")

        if msg_type == "system" and data.get("subtype") == "init":
            self.session_id = data.get("session_id", "") or None
            log.debug(
                "[pool:%s] streaming init: session=%s",
                self.pool_id,
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
            elif event_type == "content_block_delta":
                delta = event_data.get("delta", {})
                if delta.get("type") == "text_delta":
                    text = delta.get("text", "")
                    if text:
                        self._had_text_delta = True
                        self._pending.append(TextLlmEvent(text=text))

        elif msg_type == "result":
            sid = data.get("session_id", "")
            if sid:
                self.session_id = sid
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
                    self.pool_id,
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
                    self.pool_id,
                    subtype,
                    self._had_text_delta,
                    data.get("duration_ms", 0),
                    (data.get("result") or "")[:200],
                )
            log.info(
                "[pool:%s] streaming result: %dms",
                self.pool_id,
                data.get("duration_ms", 0),
            )
            self._done = True
            self._pending.append(
                ResultLlmEvent(
                    is_error=is_error,
                    duration_ms=data.get("duration_ms", 0),
                    cost_usd=None,
                    error_text=self.error if is_error else None,
                )
            )

        return self._pending
