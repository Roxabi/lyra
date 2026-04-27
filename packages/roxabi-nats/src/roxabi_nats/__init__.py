"""roxabi_nats — NATS transport SDK shared between Lyra hub and plugins.

See docs/architecture/adr/045-roxabi-nats-sdk-uv-workspace-extraction.mdx
and docs/architecture/adr/044-lyra-voicecli-nats-contract.mdx.

Public API: only the names in ``__all__`` are part of the stable external
contract. ``_``-prefixed submodules (``_serialize``, ``_sanitize``,
``_validate``, ``_version_check``, ``_tts_constants``) are hub-internal and
may change without notice — external consumers MUST NOT import them.
"""

from roxabi_contracts.envelope import CONTRACT_VERSION

from ._serialize import _TypeHintResolver as TypeHintResolver
from .adapter_base import NatsAdapterBase
from .connect import nats_connect
from .driver_base import NatsDriverBase

__all__ = [
    "CONTRACT_VERSION",
    "NatsAdapterBase",
    "NatsDriverBase",
    "TypeHintResolver",
    "nats_connect",
]
