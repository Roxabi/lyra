"""TTS synthesis and dispatch helper functions.

This module owns ``AudioPipeline``, the TTS dispatch helper used by the hub
outbound path.  The ``synthesize_and_dispatch_audio`` method resolves language
and voice from PrefsStore (with fallback to Whisper-detected language), calls
the TTS engine, and dispatches the resulting ``OutboundAudio`` to the correct
adapter.  The former STT consumer loop that lived here was removed in #534
Slice 1 and replaced by ``MessagePipeline._run_stt_stage``.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import TYPE_CHECKING

from ..tts import TtsUnavailableError
from .messaging.message import (
    InboundMessage,
    OutboundAudio,
    OutboundMessage,
    Platform,
)

if TYPE_CHECKING:
    from lyra.core.agent.agent_config import AgentTTSConfig

    from .hub import Hub

log = logging.getLogger(__name__)


_LANG_MARKERS: dict[str, tuple[str, ...]] = {
    "fr": (
        " je ",
        " tu ",
        " il ",
        " elle ",
        " nous ",
        " vous ",
        " ils ",
        " de ",
        " du ",
        " te ",
        " me ",
        " se ",
        " ce ",
        " ne ",
        " est ",
        " sont ",
        " avec ",
        " dans ",
        " pour ",
        " que ",
        " qui ",
        " une ",
        " des ",
        " les ",
        " pas ",
        " cette ",
        " mais ",
        " aussi ",
        " très ",
        " bien ",
        " tout ",
        " peut ",
        " fait ",
        " plus ",
        " sur ",
        " mon ",
        " ton ",
        " son ",
        " votre ",
        " notre ",
        "c'est",
        "j'ai",
        "l'on",
        "n'est",
        "qu'il",
        "d'un",
        "d'une",
    ),
}


def _detect_language(
    text: str,
    languages: list[str] | None = None,
    default_language: str | None = None,
) -> str | None:
    """Detect language from text using marker-word heuristics.

    *languages* is the candidate list (e.g. ["fr", "en"]) from agent config.
    *default_language* is returned when text is too short or no markers match.
    Only languages with entries in _LANG_MARKERS are actively detected;
    the rest are covered by the default.
    """
    if not languages:
        return default_language
    if len(text) < 10:
        return default_language
    lower = text.lower()
    best_lang = default_language
    best_hits = 0
    for lang in languages:
        markers = _LANG_MARKERS.get(lang)
        if markers is None:
            continue
        hits = sum(1 for m in markers if m in lower)
        if hits > best_hits:
            best_hits = hits
            best_lang = lang
    return best_lang if best_hits >= 2 else default_language


class AudioPipeline:
    """TTS dispatch helper.

    Owns TTS configuration resolution and synthesis/dispatch pipeline.
    Extracted from Hub per ADR-045 Phase 2.
    """

    def __init__(self, hub: Hub) -> None:
        self._hub = hub

    def resolve_agent_tts(self, msg: InboundMessage) -> "AgentTTSConfig | None":
        """Resolve per-agent TTS config from the message's binding."""
        binding = self._hub.resolve_binding(msg)
        if binding is None:
            return None
        agent = self._hub.agent_registry.get(binding.agent_name)
        if agent is None:
            log.warning(
                "Agent %r from binding not in registry — using global TTS defaults",
                binding.agent_name,
            )
            return None
        return agent.config.voice.tts if agent.config.voice is not None else None

    def tts_language_kwargs(self, msg: InboundMessage) -> dict:
        """Build session_language + on_language_detected kwargs for TTS."""
        pool = self._resolve_pool(msg)
        if pool is None:
            return {}
        return {
            "session_language": pool.last_detected_language,
            "on_language_detected": lambda lang: setattr(
                pool,
                "last_detected_language",
                lang,
            ),
        }

    def _resolve_agent_fallback_language(self, msg: InboundMessage) -> str | None:
        """Resolve per-agent fallback_language from the message's binding (#343)."""
        binding = self._hub.resolve_binding(msg)
        if binding is None:
            return None
        agent = self._hub.agent_registry.get(binding.agent_name)
        if agent is None:
            return None
        return agent.config.i18n_language

    def _resolve_pool(self, msg: InboundMessage):
        """Resolve pool from message routing (for session-level state)."""
        from .hub.hub_protocol import RoutingKey

        key = RoutingKey(Platform(msg.platform), msg.bot_id, msg.scope_id)
        pool_id = key.to_pool_id()
        return self._hub.pools.get(pool_id)

    async def synthesize_and_dispatch_audio(  # noqa: PLR0913, C901
        self,
        msg: InboundMessage,
        text: str,
        *,
        agent_tts: "AgentTTSConfig | None" = None,
        fallback_language: str | None = None,
        on_language_detected: "Callable[[str], None] | None" = None,
        session_language: str | None = None,
    ) -> None:
        """Synthesize TTS audio for a voice response and dispatch it.

        Language/voice resolution uses PrefsStore when available; sentinels
        "detected" and "agent_default" are never forwarded to synthesize().
        ``agent_tts`` carries the per-agent TTS config (engine, voice, etc.).
        ``fallback_language`` is the agent-level language default (#343).
        Errors are logged and swallowed — audio failure must not crash the hub.
        Callers always dispatch text before invoking this method; the error
        handler therefore logs and returns only — never calls dispatch_response —
        to avoid the reentrancy loop documented in #621.
        """
        assert self._hub._tts is not None  # caller guarantees this
        try:
            lang: str | None = None
            voice: str | None = None

            if self._hub._prefs_store is not None:
                try:
                    prefs = await self._hub._prefs_store.get_prefs(msg.user_id)
                except Exception:
                    log.warning(
                        "PrefsStore.get_prefs() failed for user %s — "
                        "falling back to detected language",
                        msg.user_id,
                        exc_info=True,
                    )
                    lang = msg.language
                else:
                    # Language resolution
                    if prefs.tts_language != "detected":
                        lang = prefs.tts_language  # explicit user override
                    elif msg.language is not None:
                        lang = msg.language  # Whisper-detected
                    # else lang=None → agent_tts or global fallback

                    # Voice resolution
                    if prefs.tts_voice != "agent_default":
                        voice = prefs.tts_voice  # explicit user override
                    # else voice=None → agent_tts or global fallback
            else:
                # No PrefsStore — pass detected language directly (S1 behavior)
                lang = msg.language

            # Fallback: detect language from response text when STT didn't
            # provide one (e.g. /voice mode with typed text).
            if lang is None and text:
                _langs = agent_tts.languages if agent_tts else None
                _default = session_language or (
                    agent_tts.default_language if agent_tts else None
                )
                lang = _detect_language(
                    text,
                    languages=_langs,
                    default_language=_default,
                )
                if lang is not None and on_language_detected is not None:
                    on_language_detected(lang)

            tts_text = text.replace("\n", " ")
            result = await self._hub._tts.synthesize(
                tts_text,
                agent_tts=agent_tts,
                language=lang,
                voice=voice,
                fallback_language=fallback_language,
            )
            audio = OutboundAudio(
                audio_bytes=result.audio_bytes,
                mime_type=result.mime_type,
                duration_ms=result.duration_ms,
                waveform_b64=result.waveform_b64,
            )
            await self._hub.dispatch_audio(msg, audio)
            log.info(
                "Voice TTS dispatched: %d bytes for msg id=%s",
                len(result.audio_bytes),
                msg.id,
            )
        except Exception as _tts_exc:
            # Text response was already dispatched by the caller before TTS was
            # attempted — the user has the content.  Do not call dispatch_response
            # here: doing so creates a reentrancy path that causes an infinite
            # retry loop in production (#621).
            # Instead, use _route_outbound directly to send a notification
            # without spawning TTS tasks.
            if isinstance(_tts_exc, TtsUnavailableError):
                log.warning(
                    "TTS adapter unavailable for msg id=%s — notifying user",
                    msg.id,
                )
                _notif_text = "⚠️ Voice synthesis temporarily unavailable."
            else:
                log.exception(
                    "TTS synthesis failed (msg id=%s) — notifying user",
                    msg.id,
                )
                _notif_text = "⚠️ Voice synthesis failed."

            # Send notification via _route_outbound (safe: no TTS spawning)
            await self._hub._route_outbound(
                msg,
                enqueue_fn=lambda d: d.enqueue(
                    msg, OutboundMessage.from_text(_notif_text)
                ),
                fallback_fn=lambda a: a.send(
                    msg, OutboundMessage.from_text(_notif_text)
                ),
                resource="tts-failure-notification",
            )
