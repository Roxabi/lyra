"""TTS dispatch helper — extracted from outbound_router.py for line count management.

This module owns the TTS dispatch logic for voice responses. It handles:
- Detecting when TTS should be triggered (voice modality or speak flag)
- Creating deferred TTS tasks after text dispatch
- Managing the tee pattern for streaming responses (collect text while streaming)
- Memory task tracking for proper cleanup
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import TYPE_CHECKING

from ...messaging.message import InboundMessage, OutboundMessage, Response
from ...messaging.render_events import RenderEvent, TextRenderEvent

if TYPE_CHECKING:
    from ...tts_dispatch import AudioPipeline


class TtsDispatch:
    """TTS dispatch helper for voice responses.

    Owns TTS task creation and memory task tracking. Used by OutboundRouter
    for voice responses in both non-streaming and streaming dispatch paths.
    """

    def __init__(
        self,
        audio_pipeline: "AudioPipeline | None" = None,
        tts: "object | None" = None,
        memory_tasks: "set[asyncio.Task] | None" = None,
    ) -> None:
        self._audio_pipeline = audio_pipeline
        self._tts = tts
        self._memory_tasks = memory_tasks

    def set_audio_pipeline(self, pipeline: "AudioPipeline | None") -> None:
        """Update audio pipeline reference."""
        self._audio_pipeline = pipeline

    def set_tts(self, tts: "object | None") -> None:
        """Update TTS reference."""
        self._tts = tts

    def set_memory_tasks(self, tasks: "set[asyncio.Task] | None") -> None:
        """Update memory tasks set reference."""
        self._memory_tasks = tasks

    def should_speak(
        self, msg: InboundMessage, response: Response | OutboundMessage
    ) -> bool:
        """Check if TTS should be triggered for this message/response."""
        if self._tts is None or self._audio_pipeline is None:
            return False
        return msg.modality == "voice" or (
            isinstance(response, Response) and response.speak
        )

    async def dispatch_tts_for_response(
        self, msg: InboundMessage, outbound: OutboundMessage
    ) -> None:
        """Dispatch TTS as a background task after response text is sent.

        Called by OutboundRouter.dispatch_response after the text response
        has been enqueued/sent.
        """
        if self._audio_pipeline is None or self._tts is None:
            return
        text = outbound.to_text().strip()
        if not text:
            return
        agent_tts = self._audio_pipeline.resolve_agent_tts(msg)
        fallback_lang = self._audio_pipeline._resolve_agent_fallback_language(msg)
        task = asyncio.create_task(
            self._audio_pipeline.synthesize_and_dispatch_audio(
                msg,
                text,
                agent_tts=agent_tts,
                fallback_language=fallback_lang,
                **self._audio_pipeline.tts_language_kwargs(msg),
            ),
            name=f"tts:{msg.id}",
        )
        if self._memory_tasks is not None:
            self._memory_tasks.add(task)
            task.add_done_callback(self._memory_tasks.discard)

    def create_streaming_tee(
        self, chunks: AsyncIterator["RenderEvent"]
    ) -> tuple[AsyncIterator["RenderEvent"], list[str], asyncio.Event]:
        """Create a tee iterator that collects text while forwarding events.

        Returns:
            - The tee iterator to pass to the streaming dispatch path
            - A list that will collect text parts
            - An Event that will be set when the tee iterator completes
        """
        voice_parts: list[str] = []
        voice_done = asyncio.Event()

        async def _tee() -> AsyncIterator["RenderEvent"]:
            try:
                async for event in chunks:
                    if isinstance(event, TextRenderEvent):
                        voice_parts.append(event.text)
                    # ToolSummaryRenderEvent: skip — voice only needs text
                    yield event
            finally:
                voice_done.set()

        return _tee(), voice_parts, voice_done

    def create_deferred_tts_task(
        self,
        msg: InboundMessage,
        voice_parts: list[str],
        voice_done: asyncio.Event,
    ) -> asyncio.Task[None] | None:
        """Create a deferred TTS task without awaiting it.

        Used when the caller needs to return immediately but still wants
        TTS to run in the background after streaming completes.
        Returns the created task, or None if TTS is not available.
        """
        if self._audio_pipeline is None or self._tts is None:
            return None

        async def _deferred_tts() -> None:
            await voice_done.wait()
            full_text = "".join(voice_parts).strip()
            if not full_text:
                return
            assert self._audio_pipeline is not None
            audio_pipeline = self._audio_pipeline
            agent_tts = audio_pipeline.resolve_agent_tts(msg)
            fallback_lang = audio_pipeline._resolve_agent_fallback_language(msg)
            await audio_pipeline.synthesize_and_dispatch_audio(
                msg,
                full_text,
                agent_tts=agent_tts,
                fallback_language=fallback_lang,
                **audio_pipeline.tts_language_kwargs(msg),
            )

        task = asyncio.create_task(_deferred_tts(), name=f"tts:{msg.id}")
        if self._memory_tasks is not None:
            self._memory_tasks.add(task)
            task.add_done_callback(self._memory_tasks.discard)
        return task

    async def dispatch_tts_from_parts(
        self, msg: InboundMessage, voice_parts: list[str]
    ) -> None:
        """Dispatch TTS immediately from pre-collected text parts.

        Used when streaming has already completed (fallback path) and text
        is already collected. Creates a background task.
        """
        if self._audio_pipeline is None or self._tts is None:
            return
        full_text = "".join(voice_parts).strip()
        if not full_text:
            return
        assert self._audio_pipeline is not None
        audio_pipeline = self._audio_pipeline
        agent_tts = audio_pipeline.resolve_agent_tts(msg)
        fallback_lang = audio_pipeline._resolve_agent_fallback_language(msg)
        task = asyncio.create_task(
            audio_pipeline.synthesize_and_dispatch_audio(
                msg,
                full_text,
                agent_tts=agent_tts,
                fallback_language=fallback_lang,
                **audio_pipeline.tts_language_kwargs(msg),
            ),
            name=f"tts:{msg.id}",
        )
        if self._memory_tasks is not None:
            self._memory_tasks.add(task)
            task.add_done_callback(self._memory_tasks.discard)
