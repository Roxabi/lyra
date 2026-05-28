"""Outbound-audio NATS contract surface.

Public API: ``OutboundAudioSubjects`` subject helper and ``STREAM_AUDIO``
stream-name constant.  No Pydantic models yet — this module ships the
subject grammar and stream anchor only; message schemas arrive in a
later minor bump once the consumer contract is finalised.
"""

from __future__ import annotations

from roxabi_contracts.outbound.subjects import STREAM_AUDIO, OutboundAudioSubjects

__all__ = [
    "OutboundAudioSubjects",
    "STREAM_AUDIO",
]
