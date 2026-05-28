"""Outbound-audio NATS subject strings and stream name constant."""

from __future__ import annotations

from roxabi_contracts._nats_utils import _validate_subject_segment

__all__ = ["OutboundAudioSubjects", "STREAM_AUDIO"]

#: JetStream stream that captures all ``lyra.outbound.audio.*`` messages.
STREAM_AUDIO = "LYRA_OUTBOUND_AUDIO"


class OutboundAudioSubjects:
    """Subject helpers for the outbound-audio domain.

    Methods are static: the class is a typed namespace, not a singleton
    instance.  The audio subject follows a 5-token grammar:
    ``lyra.outbound.audio.<platform>.<bot_id>``.

    Platform and bot_id are validated against the NATS-subject-safe
    character class ``[A-Za-z0-9_-]+`` — dots, wildcards (``*``) and
    subtree operators (``>``) are rejected to prevent subject injection.
    """

    @staticmethod
    def audio(platform: str, bot_id: str) -> str:
        """Return the per-bot outbound audio subject.

        Pattern: ``lyra.outbound.audio.<platform>.<bot_id>``

        Raises ``ValueError`` if either token contains characters outside
        ``[A-Za-z0-9_-]`` (NATS wildcard / subtree / dot injection guard).
        """
        _validate_subject_segment(platform)
        _validate_subject_segment(bot_id)
        return f"lyra.outbound.audio.{platform}.{bot_id}"
