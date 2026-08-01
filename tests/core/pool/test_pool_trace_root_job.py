"""TraceContext root_job_id always stamped for pool turns (#2147)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

from factory.core.envelope_fields import mint_work_envelope_fields
from factory.core.pool.pool_trace_context import turn_trace_context
from factory.core.trace import TraceContext


def test_turn_trace_mints_root_job_id_when_msg_lacks_one() -> None:
    msg = SimpleNamespace(trace_id=None, root_job_id=None)
    with turn_trace_context(cast(Any, msg), pool_id="web:smoke:a", agent_name="lyra"):
        jid = TraceContext.get_root_job_id()
        assert jid
        # Drivers mint envelopes from ambient root — must match registry key.
        fields = mint_work_envelope_fields(trace_id=TraceContext.get_trace_id())
        assert fields.job_id == jid
    assert TraceContext.get_root_job_id() is None


def test_turn_trace_preserves_msg_root_job_id() -> None:
    msg = SimpleNamespace(trace_id="t" * 32, root_job_id="explicit-wire-id")
    with turn_trace_context(cast(Any, msg), pool_id="p", agent_name="a"):
        assert TraceContext.get_root_job_id() == "explicit-wire-id"
