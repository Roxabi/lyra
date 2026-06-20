"""Wire codec helpers for CliPoolNatsWorker — chunk/ack serialisation."""

from __future__ import annotations

from typing import Any

from roxabi_contracts.cli.models import CliChunkEvent, CliControlAck
from roxabi_contracts.envelope import CONTRACT_VERSION


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
