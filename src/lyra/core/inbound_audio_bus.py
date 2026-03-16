"""InboundAudioBus — backwards-compatible alias for InboundBus[InboundAudio].

The implementation is the generic ``InboundBus[T]`` in ``inbound_bus.py``.
This module exists so existing import sites can continue to use
``from .inbound_audio_bus import InboundAudioBus`` without change.
"""

from __future__ import annotations

from .inbound_bus import InboundBus
from .message import InboundAudio

# InboundAudioBus is InboundBus specialised for audio envelopes.
# The name= parameter drives feeder task names and log messages.
InboundAudioBus = InboundBus[InboundAudio]

__all__ = ["InboundAudioBus"]
