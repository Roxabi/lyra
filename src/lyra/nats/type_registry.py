"""Lyra-side registry of TYPE_CHECKING-only types needed at NATS deserialization.

Single source of truth. Every Lyra NATS consumer that calls any
``roxabi_nats._serialize.deserialize*`` function imports
``TYPE_REGISTRY_RESOLVER`` from this module and passes it in — either as a
constructor kwarg (for class-based consumers: ``NatsBus``,
``NatsRenderEventCodec``, ``NatsChannelProxy``, ``NatsOutboundListener``) or
as a function parameter (for module-level free functions in
``nats_envelope_handlers`` and the ``_inbound_cache`` factory).

Fail-fast: any drift (module renamed, type deleted) raises ``ValueError`` at
module import, not at first message. This replaces the old process-global
``_TYPE_CHECKING_IMPORTS`` registry removed in #729.

Defaults semantics: every Lyra consumer sets
``resolver=TYPE_REGISTRY_RESOLVER`` as the default argument so downstream
callers (bootstrap, tests) can construct them with no explicit
resolver argument and still get correct ``CommandContext`` resolution.
Tests that need alternative resolvers pass them explicitly.
"""

from __future__ import annotations

from roxabi_nats import TypeHintResolver

TYPE_REGISTRY: tuple[tuple[str, str], ...] = (
    ("lyra.core.commands.command_parser", "CommandContext"),
)

TYPE_REGISTRY_RESOLVER = TypeHintResolver(TYPE_REGISTRY)

__all__ = ["TYPE_REGISTRY", "TYPE_REGISTRY_RESOLVER"]
