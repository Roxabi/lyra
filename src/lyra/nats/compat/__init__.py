"""NATS compat handlers for transitional deployments.

Currently contains InboundAudioLegacyHandler — Phase 1 of issue #534 —
which bridges adapters still publishing on the deprecated
``lyra.inbound.audio.*`` subject tree into the unified ``InboundMessage``
inbound path. Delete this entire package in Phase 2 of #534 once all
adapters have migrated to the single inbound subject.
"""

from lyra.nats.compat.inbound_audio_legacy import InboundAudioLegacyHandler

__all__ = ["InboundAudioLegacyHandler"]
