"""lyra.nats — hub-coupled NATS transport modules.

Transport primitives (NatsAdapterBase, nats_connect, circuit breaker,
version checks, sanitizers, serializer) now live in the roxabi_nats
package — see docs/architecture/adr/045-*.mdx.
"""

from roxabi_nats import register_type_checking_import

from .nats_bus import NatsBus
from .nats_channel_proxy import NatsChannelProxy
from .render_event_codec import NatsRenderEventCodec

# Register lyra-specific TYPE_CHECKING imports with the SDK serializer.
# CommandContext is referenced under TYPE_CHECKING in lyra dataclasses so
# roxabi_nats' serializer can rehydrate it when needed. The SDK has no
# domain knowledge — registration happens here once, at import time.
register_type_checking_import("lyra.core.commands.command_parser", "CommandContext")

__all__ = [
    "NatsBus",
    "NatsChannelProxy",
    "NatsRenderEventCodec",
]
