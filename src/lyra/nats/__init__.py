"""lyra.nats — hub-coupled NATS transport modules.

Transport primitives (NatsAdapterBase, nats_connect, circuit breaker,
version checks, sanitizers, serializer) now live in the roxabi_nats
package — see docs/architecture/adr/045-*.mdx.

The TYPE_CHECKING-only hint registry that used to live here as a
process-global has moved to an explicit per-consumer resolver; see
``lyra.nats.type_registry`` for the single source of truth.
"""

from .nats_bus import NatsBus
from .nats_channel_proxy import NatsChannelProxy
from .render_event_codec import NatsRenderEventCodec

__all__ = [
    "NatsBus",
    "NatsChannelProxy",
    "NatsRenderEventCodec",
]
