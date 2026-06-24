"""Concrete middleware stages for the inbound message pipeline (#431).

Stages 0–9 (trace, guards, pool/message prep, agent authz, command dispatch);
stage 10 (pool submit + session resume) lives in ``middleware_submit.py``.

This module re-exports from split files for backward compatibility:
- `middleware_guards.py` — Stages 0–4
- `middleware_authz.py` — Stage 7 (agent authorization, ADR-090 §5)
- `middleware_pool.py` — Stages 6, 8–9 (pool/message prep, command dispatch)
"""

from .middleware_authz import AuthorizeAgentMiddleware
from .middleware_guards import (
    RateLimitMiddleware,
    ResolveTrustMiddleware,
    TraceMiddleware,
    TrustGuardMiddleware,
    ValidatePlatformMiddleware,
)
from .middleware_pool import (
    CommandMiddleware,
    MessagePrepMiddleware,
    ResolveBindingMiddleware,
)

__all__ = [
    "AuthorizeAgentMiddleware",
    "CommandMiddleware",
    "MessagePrepMiddleware",
    "RateLimitMiddleware",
    "ResolveBindingMiddleware",
    "ResolveTrustMiddleware",
    "TraceMiddleware",
    "TrustGuardMiddleware",
    "ValidatePlatformMiddleware",
]
