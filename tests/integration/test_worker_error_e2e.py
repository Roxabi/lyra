"""Integration test skeleton: WorkerError end-to-end flow.

Spec: artifacts/specs/1016-worker-error-envelope-spec.mdx §C6
Plan: artifacts/plans/1016-worker-error-envelope-plan.mdx T3 / T19

This skeleton is skipped until Wave 6 implementation tasks (T13–T18) land.
Remove the ``@pytest.mark.skip`` decorator in T19 (RED-GATE S2) and wire up the
full assertions.

Scenario
--------
A clipool worker emits a ``CliChunkEvent`` with ``worker_error`` populated
(simulating a session-expiry exception path).  The hub's ``_extract_worker_error``
helper reads it.  ``StreamProcessor`` renders ``WorkerError.message`` as the
final ``TextRenderEvent.text``.  Two ``METRIC`` log lines are captured.

Mocked (externals only):
  - NATS transport / clipool worker (replaced by a fake async generator)

Real (not mocked — the module under test wires together real code):
  - ``StreamProcessor`` (core.processors)
  - ``_extract_worker_error`` (core.messaging.error_extractor) — once it exists
  - ``WorkerError`` (roxabi_contracts.errors) — once it exists
  - ``TextRenderEvent`` (core.messaging.render_events)
  - logging (stdlib ``caplog`` fixture)
"""

from __future__ import annotations

import logging
from collections.abc import AsyncGenerator
from typing import TYPE_CHECKING

import pytest

# ---------------------------------------------------------------------------
# Deferred imports — symbols do not exist yet (Wave 6 implements them).
# Guarded inside the test body; the file must still COLLECT cleanly.
# ---------------------------------------------------------------------------

if TYPE_CHECKING:
    from lyra.core.messaging.events import LlmEvent


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_EXPECTED_CODE = "cli.session_lost"
_EXPECTED_DOMAIN = "cli"
_EXPECTED_MESSAGE = "CLI session expired — please retry."


# ---------------------------------------------------------------------------
# Helpers / stubs
# ---------------------------------------------------------------------------


async def _fake_event_stream(
    result_event: "LlmEvent",
) -> "AsyncGenerator[LlmEvent, None]":
    """Yield a single ResultLlmEvent carrying the worker_error via error_text shim.

    Pre-T18, StreamProcessor reads ``event.error_text``; post-T18 it reads
    ``_extract_worker_error(reply).message``.  The skeleton records the shape
    expected after T18: ``error_text`` is None so the processor MUST fall
    through to ``worker_error``.
    """
    yield result_event


# ---------------------------------------------------------------------------
# Integration test
# ---------------------------------------------------------------------------


class TestWorkerErrorE2E:
    """End-to-end: clipool exception → WorkerError envelope → rendered message."""

    @pytest.mark.asyncio
    async def test_worker_error_populated_extracted_rendered_and_logged(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """C6 full assertion: populated → extracted → rendered → METRIC lines.

        Assertions (wired in T19):
          (a) ``result_event.worker_error`` has expected ``code`` and non-empty
              ``message``.
          (b) ``_extract_worker_error(result_event)`` returns the same
              ``WorkerError``.
          (c) ``TextRenderEvent.text == WorkerError.message`` (not the hardcoded
              fallback string "Something went wrong. Please try again.").
          (d) ``caplog`` contains both METRIC log lines:
                - ``METRIC worker_error_populated_total domain=cli count=1``
                - ``METRIC worker_error_received_total code=cli.session_lost
                  domain=cli count=1``

        Design notes
        ------------
        - ``ResultLlmEvent`` carries ``worker_error`` directly (T14/T15 field).
          ``_extract_worker_error`` uses ``getattr`` so it works on any envelope
          type, including ``ResultLlmEvent``.
        - ``emit_populated_total`` is called explicitly here to simulate the
          worker-side metric (T13 path).  The hub-side metric
          (``emit_received_total``) is emitted by ``StreamProcessor`` when it
          processes the ``ResultLlmEvent`` via ``_extract_worker_error`` (T18).
        """
        # ------------------------------------------------------------------
        # Arrange
        # ------------------------------------------------------------------
        from lyra.core.messaging.error_extractor import _extract_worker_error
        from lyra.core.messaging.events import ResultLlmEvent
        from lyra.core.messaging.metrics import emit_populated_total
        from lyra.core.messaging.render_events import TextRenderEvent
        from lyra.core.messaging.tool_display_config import ToolDisplayConfig
        from lyra.core.processors.stream_processor import StreamProcessor
        from roxabi_contracts.errors import WorkerError

        worker_error = WorkerError(
            code=_EXPECTED_CODE,
            message=_EXPECTED_MESSAGE,
            retryable=True,
        )

        # ResultLlmEvent carries worker_error (T14/T15 field).
        # error_text is None so the processor MUST fall through to worker_error.
        result_event = ResultLlmEvent(
            is_error=True,
            duration_ms=0,
            error_text=None,
            worker_error=worker_error,
        )

        # ------------------------------------------------------------------
        # Act — simulate worker side metric + run through StreamProcessor
        # ------------------------------------------------------------------
        with caplog.at_level(logging.INFO):
            # Simulate worker populating the envelope (T13 path) — emits the
            # "populated" METRIC line as a worker-side side effect.
            emit_populated_total(domain=_EXPECTED_DOMAIN)

            # (b) extraction — same call the hub would make
            extracted = _extract_worker_error(result_event)

            # (c) rendering — processor calls _extract_worker_error internally
            # and emits emit_received_total (T18 path).
            processor = StreamProcessor(config=ToolDisplayConfig())
            render_events = [
                event
                async for event in processor.process(_fake_event_stream(result_event))
            ]

        # ------------------------------------------------------------------
        # Assert (a) — ResultLlmEvent carries expected WorkerError
        # ------------------------------------------------------------------
        assert result_event.worker_error is not None
        assert result_event.worker_error.code == _EXPECTED_CODE
        assert result_event.worker_error.message  # non-empty

        # ------------------------------------------------------------------
        # Assert (b) — _extract_worker_error returns the same WorkerError
        # ------------------------------------------------------------------
        assert extracted is not None
        assert extracted.code == _EXPECTED_CODE
        assert extracted.message == _EXPECTED_MESSAGE

        # ------------------------------------------------------------------
        # Assert (c) — rendered text equals WorkerError.message, not fallback
        # ------------------------------------------------------------------
        text_events = [e for e in render_events if isinstance(e, TextRenderEvent)]
        assert text_events, "StreamProcessor must emit at least one TextRenderEvent"
        final_text_event = next(e for e in text_events if e.is_final)
        assert final_text_event.text == _EXPECTED_MESSAGE
        assert final_text_event.text != "Something went wrong. Please try again."
        assert final_text_event.is_error is True

        # ------------------------------------------------------------------
        # Assert (d) — both METRIC log lines captured
        # ------------------------------------------------------------------
        log_messages = [r.getMessage() for r in caplog.records]

        populated_line = (
            f"METRIC worker_error_populated_total domain={_EXPECTED_DOMAIN} count=1"
        )
        received_line = (
            f"METRIC worker_error_received_total"
            f" code={_EXPECTED_CODE} domain={_EXPECTED_DOMAIN} count=1"
        )

        assert any(populated_line in msg for msg in log_messages), (
            f"Expected log line not found: {populated_line!r}\nCaptured: {log_messages}"
        )
        assert any(received_line in msg for msg in log_messages), (
            f"Expected log line not found: {received_line!r}\nCaptured: {log_messages}"
        )
