"""lyra.nats — hub-coupled NATS transport modules.

Transport primitives (NatsAdapterBase, nats_connect, circuit breaker,
version checks, sanitizers, serializer) now live in the roxabi_nats
package — see docs/architecture/adr/045-*.mdx.
"""

# Hub-internal wiring: roxabi_nats._serialize exposes an internal registry
# for TYPE_CHECKING-only type hints that must be resolved at deserialization
# time. Lyra, as the workspace host, is the only permitted caller of this
# private helper — external SDK consumers must not import from _-prefixed
# submodules. Tracked for refactor in issue #729 (per-adapter explicit init
# param, replacing the global mutable registry).
from roxabi_nats._serialize import _register_type_checking_import

from .nats_bus import NatsBus
from .nats_channel_proxy import NatsChannelProxy
from .render_event_codec import NatsRenderEventCodec

_register_type_checking_import("lyra.core.commands.command_parser", "CommandContext")

__all__ = [
    "NatsBus",
    "NatsChannelProxy",
    "NatsRenderEventCodec",
]
