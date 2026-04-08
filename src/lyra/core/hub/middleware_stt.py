"""STT middleware stage — transcribe voice messages inline in the pipeline (#534).

Inserted between RateLimitMiddleware and ResolveBindingMiddleware so that:
- Rate limiting applies before expensive STT transcription.
- Trust resolution (ResolveTrustMiddleware) runs earlier via the middleware chain.
- Binding lookup uses the transcribed text (correct for command detection).

Replaces the deleted AudioPipeline.run() consumer loop. All 6 error outcomes
(unsupported, unavailable, noise, invalid, failed, timeout) and the re-entrance
guard are preserved from the original implementation.
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import os
import tempfile
from pathlib import Path
from typing import TYPE_CHECKING

from ..message import InboundMessage, Response
from .message_pipeline import _DROP, PipelineResult

if TYPE_CHECKING:
    from .middleware import Next, PipelineContext

log = logging.getLogger(__name__)

MAX_TRANSCRIPT_LEN = 2000

# Module-level outcome counters (Slice 1 — no metrics backend yet).
# TODO(#534-slice2): wire to a proper metrics/counter module.
_STT_STAGE_OUTCOMES: dict[str, int] = {
    "success": 0,
    "unsupported": 0,
    "unavailable": 0,
    "noise": 0,
    "invalid": 0,
    "failed": 0,
}

_MIME_TO_EXT: dict[str, str] = {
    "audio/ogg": ".ogg",
    "audio/mpeg": ".mp3",
    "audio/mp4": ".m4a",
    "audio/wav": ".wav",
    "audio/webm": ".webm",
}


def _build_stt_reply(
    msg: InboundMessage, *, reply: bool = True,
) -> InboundMessage:
    """Construct synthetic reply envelope for STT error/echo dispatch."""
    meta = dict(msg.platform_meta)
    if not reply:
        meta.pop("message_id", None)
    return dataclasses.replace(
        msg, text="", text_raw="", audio=None, modality="text", platform_meta=meta,
    )


class SttMiddleware:
    """Transcribe voice messages inline, or drop with an error reply.

    On ``modality != "voice"`` or ``text`` already populated: pass through.
    On STT success: populate ``text`` / ``text_raw`` / ``language``, strip
    ``audio``, echo transcript to user, then continue the pipeline with the
    updated message.
    On any STT failure: dispatch the appropriate ``stt_*`` template reply
    and return ``_DROP``.
    """

    async def __call__(  # noqa: C901, PLR0915
        self,
        msg: InboundMessage,
        ctx: PipelineContext,
        next: Next,
    ) -> PipelineResult:
        # 1. Modality skip — text messages pass through untouched.
        if msg.modality != "voice":
            return await next(msg, ctx)

        # 2. Re-entrance guard — voice message already has text.
        if msg.text != "":
            return await next(msg, ctx)

        hub = ctx.hub
        if hub._msg_manager is None:
            return _DROP

        # 3. STT not configured → inform user and drop.
        if hub._stt is None:
            content = hub._msg_manager.get("stt_unsupported")
            await hub.dispatch_response(
                _build_stt_reply(msg, reply=False),
                Response(content=content),
            )
            _STT_STAGE_OUTCOMES["unsupported"] += 1
            return _DROP

        # 4. Transcription block.
        timeout_s = getattr(hub._stt, "timeout_ms", 30000) / 1000.0
        if msg.audio is None:  # guaranteed by modality == "voice", guard for -O safety
            return _DROP
        suffix = _MIME_TO_EXT.get(msg.audio.mime_type, ".ogg")

        def _write_temp(data: bytes, ext: str) -> str:
            fd, path = tempfile.mkstemp(suffix=ext)
            try:
                os.write(fd, data)
            except BaseException:
                os.close(fd)
                Path(path).unlink(missing_ok=True)
                raise
            os.close(fd)
            return path

        tmp_path_str = await asyncio.to_thread(
            _write_temp, msg.audio.audio_bytes, suffix,
        )
        tmp_path = Path(tmp_path_str)

        try:
            result = await asyncio.wait_for(
                hub._stt.transcribe(tmp_path),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            log.warning("STT timed out for msg id=%s", msg.id)
            await self._dispatch_error(hub, msg, "stt_failed")
            _STT_STAGE_OUTCOMES["failed"] += 1
            return _DROP
        except Exception as exc:
            from lyra.stt import STTNoiseError, STTUnavailableError

            if isinstance(exc, STTNoiseError):
                log.info("STT noise for msg id=%s: %s", msg.id, exc)
                await self._dispatch_error(hub, msg, "stt_noise")
                _STT_STAGE_OUTCOMES["noise"] += 1
                return _DROP
            if isinstance(exc, STTUnavailableError):
                log.warning("STT unavailable for msg id=%s: %s", msg.id, exc)
                await self._dispatch_error(hub, msg, "stt_unavailable")
                _STT_STAGE_OUTCOMES["unavailable"] += 1
                return _DROP
            log.exception("STT failed for msg id=%s", msg.id)
            await self._dispatch_error(hub, msg, "stt_failed")
            _STT_STAGE_OUTCOMES["failed"] += 1
            return _DROP
        finally:
            tmp_path.unlink(missing_ok=True)

        # 5. Post-transcription guards (noise filtering is owned by the STT adapter).
        transcript = result.text.strip()

        if transcript.startswith("/"):
            log.warning("msg id=%s: slash-command injection guard", msg.id)
            await self._dispatch_error(hub, msg, "stt_invalid")
            _STT_STAGE_OUTCOMES["invalid"] += 1
            return _DROP

        if len(transcript) > MAX_TRANSCRIPT_LEN:
            log.warning("msg id=%s: transcript too long (%d)", msg.id, len(transcript))
            await self._dispatch_error(hub, msg, "stt_invalid")
            _STT_STAGE_OUTCOMES["invalid"] += 1
            return _DROP

        # 6. Success — echo transcript and continue pipeline.
        await hub.dispatch_response(
            _build_stt_reply(msg, reply=False),
            Response(content=f"\U0001f3a4 {transcript}"),
        )
        updated = dataclasses.replace(
            msg,
            text=transcript,
            text_raw=f"\U0001f3a4 {transcript}",
            language=result.language,
            audio=None,
        )
        _STT_STAGE_OUTCOMES["success"] += 1
        return await next(updated, ctx)

    @staticmethod
    async def _dispatch_error(hub: object, msg: InboundMessage, key: str) -> None:
        """Dispatch an STT error reply using the given message template key."""
        content = hub._msg_manager.get(key)  # type: ignore[union-attr]
        await hub.dispatch_response(  # type: ignore[union-attr]
            _build_stt_reply(msg, reply=True),
            Response(content=content),
        )
