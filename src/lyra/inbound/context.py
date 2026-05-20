"""Inbound pipeline context dataclasses.

Frozen-container, mutable-contents contract (NC4 resolution)
-------------------------------------------------------------
All four dataclasses are ``@dataclass(frozen=True)``: the *container references*
are immutable (you cannot rebind ``ctx.router``, ``ctx.router.owned_threads``,
etc.).  However, certain fields hold **mutable objects that are mutated in place**
by pipeline stages:

- ``RouterCtx.owned_threads`` — ``set[int]`` mutated by ``pre_route_hook``
  (e.g. ``owned_threads.add(thread_id)`` after cold-path ``is_owned`` warmup or
  auto-thread creation).
- ``SessionCtx.thread_sessions_cache`` — ``dict[str, ThreadSession]`` written
  through by ``SessionBuilder`` via ``persist_thread_session``.

This is intentional: mutable refs are **shared by reference** with the adapter
instance so warm-up state persists across messages within a single adapter
lifecycle.  The frozen container provides a structural guarantee that no stage can
accidentally swap in a different set/dict; it does not guarantee that the contents
are unchanged.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.adapters.shared._shared import TypingTaskManager
    from lyra.adapters.shared.outbound_listener import OutboundListener
    from lyra.core.circuit_breaker import CircuitRegistry
    from lyra.core.messaging.bus import Bus
    from lyra.core.messaging.messages import MessageManager
    from lyra.core.stores.thread_store_protocol import (
        ThreadSession,
        ThreadStoreProtocol,
    )
    from lyra.infrastructure.stores.turn_store import TurnStore


@dataclass(frozen=True)
class RouterCtx:
    """Routing-stage context.

    ``owned_threads`` and ``watch_channels`` are read by ``Router.decide``.
    ``owned_threads`` is a **mutable set** (frozen-container, mutable-contents
    contract): stages such as ``pre_route_hook`` may call ``owned_threads.add()``
    to warm the hot set from cold-path I/O without rebuilding the context.
    """

    bot_id: str
    owned_threads: set[int]
    watch_channels: frozenset[int] | None


@dataclass(frozen=True)
class SessionCtx:
    """Session-building-stage context.

    ``thread_sessions_cache`` is a **mutable dict** (frozen-container,
    mutable-contents contract): ``SessionBuilder`` writes through to it via
    ``persist_thread_session`` so cached entries survive across messages.
    """

    turn_store: TurnStore | None
    thread_store: ThreadStoreProtocol | None
    thread_sessions_cache: dict[str, ThreadSession] = field(default_factory=dict)


@dataclass(frozen=True)
class DispatchCtx:
    """Dispatch-stage context."""

    inbound_bus: Bus[object]
    circuit_registry: CircuitRegistry | None
    outbound_listener: OutboundListener | None
    typing: TypingTaskManager
    msg_catalog: MessageManager | None


@dataclass(frozen=True)
class InboundContext:
    """Composite context threaded through the entire inbound pipeline.

    Each stage receives only its sub-context (``router``, ``session``, or
    ``dispatch``).  The top-level ``InboundContext`` is passed to
    ``InboundPipeline.run`` which fans it out to the appropriate stage.
    """

    router: RouterCtx
    session: SessionCtx
    dispatch: DispatchCtx
