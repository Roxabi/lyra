"""Pure helpers for CliPoolNatsWorker — error classification + wire codec."""

from __future__ import annotations

from typing import Any

from roxabi_contracts.cli.models import CliChunkEvent, CliControlAck
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.errors import WorkerError


def _classify_exception(exc: BaseException) -> WorkerError:
    """Map an exception to a ``WorkerError`` with the appropriate code.

    Code selection (per T13 / ADR-066 (absorbed into ADR-049)):
    - ``asyncio.TimeoutError`` → ``cli.session_lost`` (retryable=True)
    - ``UnicodeDecodeError`` / ``ValueError`` (parse/decode) → ``cli.parse``
      (retryable=False)
    - Any other exception → ``worker.crash`` (retryable=True)
    """
    import asyncio

    # Sanitize bus-bound messages (#1215, sibling of #1212). Exception __str__
    # can embed file paths, byte sequences, or arbitrary text from system
    # errors — the bus-bound message keeps only the type name. Full traceback
    # logging is owned by the callers (_handle_cmd_streaming/_handle_cmd_blocking
    # both call log.exception before invoking this classifier).
    if isinstance(exc, asyncio.TimeoutError):
        return WorkerError(
            code="cli.session_lost",
            message=f"CLI session timed out: {type(exc).__name__}",
            retryable=True,
        )
    if isinstance(exc, (UnicodeDecodeError, ValueError)):
        return WorkerError(
            code="cli.parse",
            message=f"CLI parse/decode error: {type(exc).__name__}",
            retryable=False,
        )
    return WorkerError(
        code="worker.crash",
        message=f"Unhandled worker exception: {type(exc).__name__}",
        retryable=True,
    )


def _make_chunk(pool_id: str, **kwargs: Any) -> bytes:
    """Serialise a CliChunkEvent to JSON bytes for NATS publish."""
    import uuid
    from datetime import datetime, timezone

    event = CliChunkEvent(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        pool_id=pool_id,
        **kwargs,
    )
    return event.model_dump_json().encode()


def _make_ack(pool_id: str, **kwargs: Any) -> bytes:
    """Serialise a CliControlAck to JSON bytes for NATS publish."""
    import uuid
    from datetime import datetime, timezone

    ack = CliControlAck(
        contract_version=CONTRACT_VERSION,
        trace_id=str(uuid.uuid4()),
        issued_at=datetime.now(timezone.utc),
        pool_id=pool_id,
        **kwargs,
    )
    return ack.model_dump_json().encode()
