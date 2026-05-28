"""Voice undelivered notification primitives.

Shared by hub-side (nats_channel_proxy) and adapter-side
(jetstream_audio_consumer) paths to build the user-facing notification
when an outbound audio message cannot be delivered after all retries.

Tone/emoji mirrors the TTS-failure precedent in tts_dispatch.py.

No NATS or platform imports — pure data, fully testable without I/O.
"""

from __future__ import annotations

from .message import OutboundMessage

# User-facing string for terminal audio delivery failure.
# Matches the "⚠️ Voice synthesis …" tone from tts_dispatch.py.
VOICE_UNDELIVERED_MSG = "⚠️ Voice message could not be delivered."


def notify_undelivered(*, context: str | None = None) -> OutboundMessage:
    """Build the outbound notification payload for a terminal audio delivery failure.

    Returns an ``OutboundMessage`` ready to be enqueued / sent to the user.
    The caller (hub proxy or adapter consumer) sends it via the normal
    dispatch path — this helper does NOT publish to NATS directly.

    Args:
        context: Optional machine-readable label for logging (e.g.
            ``"term-retry-exceeded"`` or ``"hub-publish-fail"``).
            Never appended to the user-facing text — kept for callers that
            want to log the failure category alongside the notification.

    Returns:
        ``OutboundMessage`` whose single content part is
        ``VOICE_UNDELIVERED_MSG``.  No ``str(exc)`` is ever embedded —
        SanitizedError discipline applies to any bus-bound path.

    Example (hub proxy failure)::

        outbound = notify_undelivered(context="hub-publish-fail")
        await adapter.send(inbound_msg, outbound)

    Example (adapter terminal retry)::

        outbound = notify_undelivered(context="term-retry-exceeded")
        await self._send_text(chat_id, outbound)
    """
    # context is intentionally unused in the returned payload — it exists
    # solely as a hook for callers that want to log it before sending.
    _ = context
    return OutboundMessage.from_text(VOICE_UNDELIVERED_MSG)
