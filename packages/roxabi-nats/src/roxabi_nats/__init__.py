"""roxabi_nats — NATS transport SDK shared between Lyra hub and plugins.

See docs/architecture/adr/045-roxabi-nats-sdk-uv-workspace-extraction.mdx
and docs/architecture/adr/044-lyra-voicecli-nats-contract.mdx.
"""

from ._serialize import register_type_checking_import
from .adapter_base import CONTRACT_VERSION, NatsAdapterBase
from .connect import nats_connect

__all__ = [
    "CONTRACT_VERSION",
    "NatsAdapterBase",
    "nats_connect",
    "register_type_checking_import",
]
