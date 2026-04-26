"""Conformance test: NatsOutboundListener satisfies OutboundListener.

Runs at three levels:
1. Static (module-level) — the typed tuple ``_OUTBOUND_LISTENER_IMPLS``
   forces mypy/pyright to verify that NatsOutboundListener is assignable to
   ``type[OutboundListener]`` at type-check time. A shape drift (missing
   method, renamed method, or incompatible signature) rejects the
   assignment. Zero runtime impact beyond importing the class.
2. Static (in-test) — the ``proto: OutboundListener = listener`` annotated
   assignment inside the test runs the same check on an instance, giving a
   second signal for loose type-checker configurations.
3. Runtime — the protocol-surface test and the ``cache_inbound`` exercise
   guard against rename/removal and positional-arg drift that a loose type
   checker might miss.
"""

from __future__ import annotations

import inspect
from datetime import datetime, timezone
from unittest.mock import AsyncMock

from lyra.adapters.nats.nats_outbound_listener import NatsOutboundListener
from lyra.adapters.shared.outbound_listener import OutboundListener
from lyra.core.audio_payload import AudioPayload
from lyra.core.auth.trust import TrustLevel
from lyra.core.messaging.message import InboundMessage, Platform, TelegramMeta

# Module-level static structural check. mypy/pyright verify that
# NatsOutboundListener (the class object) is assignable to
# type[OutboundListener] — i.e. its instances structurally conform to the
# protocol. Declared as an uppercase module constant so it is not flagged
# as an unused local by type checkers.
_OUTBOUND_LISTENER_IMPLS: tuple[type[OutboundListener], ...] = (NatsOutboundListener,)


def _make_voice_message() -> InboundMessage:
    """Minimal InboundMessage(modality='voice') fixture for exercising cache_inbound.

    Slice 2 (issue #534): cache_inbound now accepts only InboundMessage.
    Voice messages carry audio via InboundMessage.audio (AudioPayload).
    """
    return InboundMessage(
        id="audio-1",
        platform="telegram",
        bot_id="main",
        scope_id="chat:42",
        user_id="tg:user:1",
        user_name="Alice",
        is_mention=False,
        text="",
        text_raw="",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(
            chat_id=42,
            topic_id=None,
            message_id=None,
            is_group=False,
        ),
        trust_level=TrustLevel.TRUSTED,
        modality="voice",
        audio=AudioPayload(
            audio_bytes=b"\x00",
            mime_type="audio/ogg",
            duration_ms=None,
            file_id=None,
        ),
    )


def test_nats_listener_satisfies_outbound_listener_protocol() -> None:
    """NatsOutboundListener structurally conforms to OutboundListener.

    The typed assignment is the static check; the runtime assertions plus
    the cache_inbound exercise guard against signature drift that a loose
    type-checker configuration might miss.
    """
    listener = NatsOutboundListener(AsyncMock(), Platform.TELEGRAM, "main", AsyncMock())
    # Static structural conformance — mypy/pyright verify this assignment.
    proto: OutboundListener = listener
    assert proto is listener
    assert hasattr(proto, "cache_inbound")
    assert callable(proto.cache_inbound)
    assert hasattr(proto, "start")
    assert inspect.iscoroutinefunction(proto.start)
    assert hasattr(proto, "stop")
    assert inspect.iscoroutinefunction(proto.stop)

    # Exercise cache_inbound with a real InboundMessage(modality='voice') to
    # catch positional signature drift (e.g. an accidental second parameter).
    # hasattr/callable alone would not fail on such a change.
    proto.cache_inbound(_make_voice_message())


def test_protocol_public_surface_is_exactly_three_methods() -> None:
    """OutboundListener exposes exactly cache_inbound, start, stop — no more.

    Scopes the check to the class's own ``vars()`` (i.e. ``__dict__``) so it
    is immune to inherited attributes that ``typing.Protocol`` or future
    ``object`` metaclass changes may expose via ``dir()``.
    """
    public = {
        name
        for name, value in vars(OutboundListener).items()
        if not name.startswith("_") and callable(value)
    }
    assert public == {"cache_inbound", "start", "stop"}, (
        f"Protocol surface drifted: got {sorted(public)}"
    )


def test_module_conformance_registry_includes_nats_listener() -> None:
    """Sanity check for the module-level static conformance registry.

    Also ensures type checkers see _OUTBOUND_LISTENER_IMPLS as referenced —
    its real value is the type annotation that forces mypy/pyright to verify
    NatsOutboundListener is assignable to type[OutboundListener].
    """
    assert NatsOutboundListener in _OUTBOUND_LISTENER_IMPLS
