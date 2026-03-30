"""InboundAudioBus — backwards-compatible alias for LocalBus[InboundAudio].

The implementation is the generic ``LocalBus[T]`` in ``inbound_bus.py``.
This module exists so existing import sites can continue to use
``from .inbound_audio_bus import InboundAudioBus`` without change.
"""

from __future__ import annotations

from .inbound_bus import LocalBus
from .message import InboundAudio

# InboundAudioBus is LocalBus specialised for audio envelopes.
# The name= parameter drives feeder task names and log messages.
InboundAudioBus = LocalBus[InboundAudio]

__all__ = ["InboundAudioBus"]
