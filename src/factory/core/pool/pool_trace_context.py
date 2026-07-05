"""TraceContext hydration for async pool workers (#2069)."""

from __future__ import annotations

from collections.abc import Generator
from contextlib import contextmanager
from contextvars import Token
from uuid import uuid4

from factory.core.messaging.message import InboundMessage
from factory.core.trace import TraceContext


@contextmanager
def pool_turn_trace_context(
    msg: InboundMessage,
    *,
    pool_id: str,
    agent_name: str,
) -> Generator[str, None, None]:
    """Re-hydrate trace correlation vars for the duration of a pool turn."""
    stamped_trace = msg.trace_id if isinstance(msg.trace_id, str) else None
    trace_id = stamped_trace or TraceContext.get_trace_id() or uuid4().hex
    token_t = TraceContext.set_trace_id(trace_id)
    token_p = TraceContext.set_pool_id(pool_id)
    token_j: Token[str] | None = None
    if isinstance(msg.root_job_id, str) and msg.root_job_id:
        token_j = TraceContext.set_root_job_id(msg.root_job_id)
    token_an = TraceContext.set_agent_name(agent_name)
    try:
        yield trace_id
    finally:
        TraceContext.reset_agent_name(token_an)
        if token_j is not None:
            TraceContext.reset_root_job_id(token_j)
        TraceContext.reset_pool_id(token_p)
        TraceContext.reset_trace_id(token_t)
