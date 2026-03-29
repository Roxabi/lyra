"""Audio processing pipeline: InboundAudio → STT → InboundMessage + TTS dispatch."""

from __future__ import annotations

import asyncio
import logging
import os
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from .message import (
    InboundAudio,
    InboundMessage,
    OutboundAudio,
    Platform,
    Response,
)
from .trust import TrustLevel

if TYPE_CHECKING:
    from lyra.core.agent_config import AgentTTSConfig

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
    """Drain InboundAudioBus, transcribe via STT, re-enqueue as text."""

    def __init__(self, hub: Hub) -> None:
        self._hub = hub

    async def run(self) -> None:
        """Drain InboundAudioBus, transcribe via STT, re-enqueue as InboundMessage.

        When STT is not configured, sends an ``stt_unsupported`` reply and drops
        the audio envelope. When transcription fails, sends an ``stt_failed``
        reply. When the transcription is noise, sends an ``stt_noise`` reply.

        Runs until cancelled.
        """
        while True:
            audio: InboundAudio = await self._hub.inbound_audio_bus.get()
            try:
                await self._process_audio_item(audio)
            except Exception:
                log.exception("audio_loop failed for audio id=%s", audio.id)
                _content = (
                    self._hub._msg_manager.get("stt_failed")
                    if self._hub._msg_manager
                    else "Sorry, I couldn't transcribe your voice message."
                )
                try:
                    await self._dispatch_audio_reply(audio, _content)
                except Exception:
                    log.exception(
                        "dispatch_audio_reply failed for audio id=%s", audio.id
                    )
            finally:
                self._hub.inbound_audio_bus.task_done()

    async def _process_audio_item(self, audio: InboundAudio) -> None:
        from ..stt import is_whisper_noise
        from .hub import RoutingKey

        try:
            platform_enum = Platform(audio.platform)
        except ValueError:
            log.warning(
                "unknown platform %r in audio id=%s — audio dropped",
                audio.platform,
                audio.id,
            )
            return
        key = RoutingKey(platform_enum, audio.bot_id, audio.scope_id)

        if audio.trust_level == TrustLevel.BLOCKED:
            log.warning("audio %s has trust_level=BLOCKED — dropped", audio.id)
            return

        # Per-user rate limit — mirrors the text pipeline check.
        _rate_key = (str(audio.platform), audio.bot_id, audio.user_id)
        if self._hub._is_rate_limited_by_key(_rate_key):
            log.warning(
                "audio rate limit exceeded for %s — audio %s dropped", key, audio.id
            )
            _rl_content = (
                self._hub._msg_manager.get("rate_limited")
                if self._hub._msg_manager
                else "You are sending messages too fast. Please slow down."
            )
            await self._dispatch_audio_reply(audio, _rl_content)
            return

        if self._hub._stt is None:
            _content = (
                self._hub._msg_manager.get("stt_unsupported")
                if self._hub._msg_manager
                else "Voice messages are not supported — STT is not configured."
            )
            await self._dispatch_audio_reply(audio, _content)
            log.warning("STT not configured — audio %s dropped", audio.id)
            return

        tmp = Path(
            await asyncio.to_thread(
                self._write_temp_audio,
                audio.audio_bytes,
                _mime_to_ext(audio.mime_type),
            )
        )
        try:
            result = await self._hub._stt.transcribe(tmp)
        finally:
            tmp.unlink(missing_ok=True)

        if is_whisper_noise(result.text):
            _content = (
                self._hub._msg_manager.get("stt_noise")
                if self._hub._msg_manager
                else "I couldn't make out your voice message, please try again."
            )
            await self._dispatch_audio_reply(audio, _content)
            log.info("STT noise for audio %s — replied with stt_noise", audio.id)
            return

        # Sanitize transcript: cap length and block slash-command injection.
        _MAX_TRANSCRIPT = 2000
        transcript = result.text[:_MAX_TRANSCRIPT]
        if transcript.lstrip().startswith("/"):
            log.warning(
                "audio %s: transcript starts with '/' — dropping to prevent"
                " slash-command injection",
                audio.id,
            )
            _content = (
                self._hub._msg_manager.get("stt_invalid")
                if self._hub._msg_manager
                else "I couldn't process that voice message."
            )
            await self._dispatch_audio_reply(audio, _content)
            return

        # Echo transcription back to user before agent processing.
        # reply=False: transcript is informational, no need to quote/mention.
        _echo = transcript[:_MAX_TRANSCRIPT]
        await self._dispatch_audio_reply(audio, f"\U0001f3a4 _{_echo}_", reply=False)

        text = f"\U0001f3a4 [voice]: {transcript}"
        msg = InboundMessage(
            id=audio.id,
            platform=audio.platform,
            bot_id=audio.bot_id,
            scope_id=audio.scope_id,
            user_id=audio.user_id,
            user_name=audio.user_name,
            is_mention=audio.is_mention,
            text=transcript,
            text_raw=text,
            timestamp=audio.timestamp,
            trust_level=audio.trust_level,
            trust=audio.trust,
            platform_meta=audio.platform_meta,
            routing=audio.routing,
            modality="voice",
            language=result.language,  # propagate Whisper-detected language
        )
        try:
            self._hub.inbound_bus.put(platform_enum, msg)
        except asyncio.QueueFull:
            log.warning("inbound bus full — transcribed audio %s dropped", audio.id)
            return
        log.info(
            "Audio %s transcribed (%s, %.1fs) → re-enqueued as text on %s",
            audio.id,
            result.language,
            result.duration_seconds,
            key,
        )

    async def _dispatch_audio_reply(
        self, audio: InboundAudio, content: str, *, reply: bool = True
    ) -> None:
        """Send an error/info reply for an audio envelope.

        Constructs a synthetic InboundMessage from the audio envelope so
        dispatch_response() can route the reply back to the originating adapter.

        When reply=False, message_id is stripped from platform_meta so the
        message is sent as a plain message without quoting/mentioning the user
        (used for the transcript echo which is informational, not a reply).
        """
        meta = audio.platform_meta
        if not reply:
            meta = {k: v for k, v in meta.items() if k != "message_id"}
        synthetic = InboundMessage(
            id=audio.id,
            platform=audio.platform,
            bot_id=audio.bot_id,
            scope_id=audio.scope_id,
            user_id=audio.user_id,
            user_name=audio.user_name,
            is_mention=False,
            text="",
            text_raw="",
            timestamp=audio.timestamp,
            trust_level=audio.trust_level,
            trust=audio.trust,
            platform_meta=meta,
            routing=audio.routing,
        )
        await self._hub.dispatch_response(synthetic, Response(content=content))

    async def synthesize_and_dispatch_audio(  # noqa: PLR0913
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

            result = await self._hub._tts.synthesize(
                text,
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
        except Exception:
            log.exception(
                "TTS synthesis failed for voice response — audio not sent (msg id=%s)",
                msg.id,
            )

    @staticmethod
    def _write_temp_audio(data: bytes, suffix: str) -> str:
        """Write *data* to a temp file (blocking I/O, run via to_thread)."""
        fd, path = tempfile.mkstemp(suffix=suffix)
        try:
            os.write(fd, data)
        except BaseException:
            os.close(fd)
            Path(path).unlink(missing_ok=True)
            raise
        os.close(fd)
        return path


def _mime_to_ext(mime_type: str) -> str:
    """Map common audio MIME types to file extensions for STT temp files."""
    _MAP = {
        "audio/ogg": ".ogg",
        "audio/mpeg": ".mp3",
        "audio/mp4": ".m4a",
        "audio/wav": ".wav",
        "audio/x-wav": ".wav",
        "audio/webm": ".webm",
    }
    return _MAP.get(mime_type, ".ogg")
