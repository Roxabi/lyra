"""Dispatch configuration constants — retry backoff and transcript limits."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class DispatchConfig:
    """Frozen constants for outbound dispatch and STT transcript validation.

    BACKOFF_DELAYS  — progressive sleep durations (seconds) between delivery
                      retry attempts (index 0 = first retry, etc.).
    MAX_ATTEMPTS    — total delivery attempts (1 initial + len(BACKOFF_DELAYS) retries).
    MAX_TRANSCRIPT_LEN — hard character limit applied to STT transcripts before
                         forwarding to the pipeline; transcripts exceeding this
                         are dropped with an ``stt_too_long`` reply.
    """

    BACKOFF_DELAYS: tuple[float, ...] = (1.0, 2.0, 4.0)
    MAX_ATTEMPTS: int = 4
    # ~8k chars ≈ 8–12 min spoken FR; long monologues still need chunking (design C).
    MAX_TRANSCRIPT_LEN: int = 8000  # const-ok: named config default
