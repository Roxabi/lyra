"""Conformance test: NatsOutboundListener satisfies OutboundListener.

Runs at two levels:
1. Static — the annotated assignment ``proto: OutboundListener = listener``
   makes mypy/pyright verify structural conformance at type-check time.
   If NatsOutboundListener's public surface drifts from OutboundListener,
   type checking fails.
2. Runtime — asserts the 3 required public methods exist and are callable
   on a real NatsOutboundListener instance, guarding against accidental
   rename/removal that the type checker alone might miss in loose configs.
"""
from __future__ import annotations

import inspect
from unittest.mock import AsyncMock

from lyra.adapters.nats_outbound_listener import NatsOutboundListener
from lyra.adapters.outbound_listener import OutboundListener
from lyra.core.message import Platform


def test_nats_listener_satisfies_outbound_listener_protocol() -> None:
    """NatsOutboundListener structurally conforms to OutboundListener.

    The typed assignment is the static check. The runtime assertions guard
    against loose type-checker configurations.
    """
    listener = NatsOutboundListener(
        AsyncMock(), Platform.TELEGRAM, "main", AsyncMock()
    )
    # Static structural conformance — mypy/pyright verify this assignment.
    proto: OutboundListener = listener
    assert proto is listener
    assert hasattr(proto, "cache_inbound")
    assert callable(proto.cache_inbound)
    assert hasattr(proto, "start")
    assert inspect.iscoroutinefunction(proto.start)
    assert hasattr(proto, "stop")
    assert inspect.iscoroutinefunction(proto.stop)


def test_protocol_public_surface_is_exactly_three_methods() -> None:
    """OutboundListener exposes exactly cache_inbound, start, stop — no more."""
    public = {
        name for name in dir(OutboundListener)
        if not name.startswith("_")
    }
    assert public == {"cache_inbound", "start", "stop"}, (
        f"Protocol surface drifted: got {sorted(public)}"
    )
