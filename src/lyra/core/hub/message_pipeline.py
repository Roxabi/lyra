"""Fail-fast message routing pipeline extracted from Hub.run()."""

from __future__ import annotations

import asyncio
import dataclasses
import enum
import logging
import os
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, Callable

from ..agent import AgentBase
from ..commands.command_parser import CommandParser
from ..message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    Platform,
    Response,
)
from ..pool import Pool

if TYPE_CHECKING:
    from .hub import Binding, Hub, RoutingKey

log = logging.getLogger(__name__)

_command_parser = CommandParser()

# Type alias for the optional trace hook.
# Called with (stage, event, **payload) at key pipeline decision points.
# The hook must be a plain synchronous callable — it is never awaited.
TraceHook = Callable[..., None]

# Maximum transcript length accepted from STT output.
# Transcripts exceeding this limit are rejected with stt_invalid.
# Mirrors the _MAX_TRANSCRIPT constant in audio_pipeline.py.
MAX_TRANSCRIPT_LEN = 2000

# T15 — STT stage outcome counters.
# No Prometheus/metrics module exists in the project yet.
# TODO(Slice 2): replace with proper observability counters (#534).
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
    "audio/flac": ".flac",
    "audio/opus": ".opus",
}


class Action(enum.Enum):
    """Terminal action for the message pipeline."""

    DROP = "drop"
    COMMAND_HANDLED = "command_handled"
    SUBMIT_TO_POOL = "submit_to_pool"


class ResumeStatus(enum.Enum):
    """Outcome of _resolve_context() — how session continuity was handled.

    RESUMED  — a session was successfully resumed via any path.
    FRESH    — Path 2 (thread-session-resume) was attempted but rejected;
               Claude will start fresh. The user should be notified.
    SKIPPED  — no resume was attempted (pool busy, group chat, first use, etc.).
               Silent: this is expected behaviour.
    """

    RESUMED = "resumed"
    FRESH = "fresh"
    SKIPPED = "skipped"


_SESSION_FALLTHROUGH_MSG = (
    "⚠️ Couldn't resume your previous session \u2014 starting fresh."
)


@dataclass(frozen=True)
class PipelineResult:
    """Immutable result from MessagePipeline.process()."""

    action: Action
    response: Response | None = None
    pool: Pool | None = None


_DROP = PipelineResult(action=Action.DROP)


class MessagePipeline:
    """Fail-fast message routing pipeline extracted from Hub.run().

    Each guard stage returns ``PipelineResult`` to stop processing or ``None``
    to continue. Terminal stages always return a ``PipelineResult``.

    An optional *trace_hook* can be supplied for observability.  It is called
    as ``trace_hook(stage, event, **payload)`` at each key decision point and
    must be a plain synchronous callable (never awaited).  When ``None``
    (the default) the hook path is a no-op with zero overhead.
    """

    def __init__(self, hub: Hub, *, trace_hook: TraceHook | None = None) -> None:
        self._hub = hub
        self._trace_hook = trace_hook

    # ------------------------------------------------------------------
    # Internal tracing helper
    # ------------------------------------------------------------------

    def _trace(self, stage: str, event: str, **payload: object) -> None:
        """Emit a trace event if a hook is registered. Never raises."""
        if self._trace_hook is None:
            return
        try:
            self._trace_hook(stage, event, **payload)
        except Exception:
            log.debug("trace_hook raised — ignoring", exc_info=True)

    async def process(
        self,
        msg: InboundMessage,
    ) -> PipelineResult:
        """Route *msg* through the pipeline stages."""
        from .hub import RoutingKey  # noqa: PLC0415 — deferred to avoid circular import

        self._trace(
            "inbound",
            "message_received",
            msg_id=msg.id,
            platform=msg.platform,
            user_id=msg.user_id,
        )

        result = self._validate_platform(msg)
        if result is not None:
            self._trace(
                "inbound",
                "platform_invalid",
                platform=msg.platform,
                action=result.action.value,
            )
            return result

        key = RoutingKey(
            Platform(msg.platform),
            msg.bot_id,
            msg.scope_id,
        )

        result = self._check_rate_limit(msg, key)
        if result is not None:
            self._trace("inbound", "rate_limited", action=result.action.value)
            return result

        msg, result = await self._run_stt_stage(msg)
        if result is not None:
            self._trace("inbound", "stt", action=result.action.value)
            return result

        binding = self._resolve_binding(msg, key)
        if binding is None:
            self._trace("pool", "no_binding", action=Action.DROP.value)
            return _DROP

        agent = self._lookup_agent(binding, key)
        if agent is None:
            self._trace(
                "pool",
                "no_agent",
                agent_name=binding.agent_name,
                action=Action.DROP.value,
            )
            return _DROP

        pool = self._hub.get_or_create_pool(
            binding.pool_id,
            binding.agent_name,
        )
        self._trace(
            "pool",
            "agent_selected",
            agent=binding.agent_name,
            pool_id=binding.pool_id,
        )

        router = getattr(agent, "command_router", None)

        cmd_ctx = _command_parser.parse(msg.text)
        if cmd_ctx is not None:
            msg = dataclasses.replace(msg, command=cmd_ctx)

        # Rewrite bare URLs to /add before command detection (#99).
        if router and hasattr(router, "prepare"):
            msg = router.prepare(msg)

        if router and router.is_command(msg):
            _cmd = msg.text.split()[0] if msg.text else ""
            self._trace("processor", "command_detected", command=_cmd)
            return await self._dispatch_command(msg, router, pool, key)

        return await self._submit_to_pool(msg, pool, key)

    # ------------------------------------------------------------------
    # T12 — STT reply envelope helper
    # ------------------------------------------------------------------

    def _build_stt_reply(
        self, msg: InboundMessage, *, reply: bool = True
    ) -> InboundMessage:
        """Construct synthetic reply envelope for STT error dispatch.

        Mirrors the behavior of AudioPipeline._dispatch_audio_reply:
        copies platform/routing fields from the source voice message, blanks
        text fields, strips the audio payload, and optionally removes
        message_id from platform_meta when the reply is informational (echo)
        rather than a direct reply to the voice message.
        """
        meta = dict(msg.platform_meta)
        if not reply:
            meta.pop("message_id", None)
        return dataclasses.replace(
            msg, text="", text_raw="", audio=None, platform_meta=meta
        )

    # ------------------------------------------------------------------
    # T13 — STT pipeline stage
    # ------------------------------------------------------------------

    async def _run_stt_stage(  # noqa: C901, PLR0915
        self, msg: InboundMessage
    ) -> tuple[InboundMessage, PipelineResult | None]:
        """STT stage — transcribe voice messages inline in the pipeline.

        Returns (msg, None) to continue the pipeline (text mode or successful
        transcription). Returns (msg, PipelineResult) to stop the pipeline
        (STT unsupported/unavailable/failed/noise/invalid, or timeout).
        """
        # 1. Modality skip — text messages pass through untouched.
        if msg.modality != "voice":
            return msg, None

        # 2. Re-entrance guard — voice message already has text (e.g. re-queued).
        if msg.text != "":
            return msg, None

        # Narrow _msg_manager for the rest of the stage (always set in Hub.__init__).
        assert self._hub._msg_manager is not None

        # 3. STT not configured → inform user and drop.
        if self._hub._stt is None:
            _unsupported_content = self._hub._msg_manager.get("stt_unsupported")
            await self._hub.dispatch_response(
                self._build_stt_reply(msg, reply=False),
                Response(content=_unsupported_content),
            )
            _STT_STAGE_OUTCOMES["unsupported"] += 1
            return msg, _DROP

        # 4. Transcription block.
        timeout_s = getattr(self._hub._stt, "timeout_ms", 30000) / 1000.0

        # Write audio bytes to a temp file (blocking I/O via to_thread).
        assert msg.audio is not None  # guaranteed: modality == "voice"
        _suffix = _MIME_TO_EXT.get(msg.audio.mime_type, ".ogg")

        def _write_temp(data: bytes, suffix: str) -> str:
            fd, path = tempfile.mkstemp(suffix=suffix)
            try:
                os.write(fd, data)
            except BaseException:
                os.close(fd)
                Path(path).unlink(missing_ok=True)
                raise
            os.close(fd)
            return path

        tmp_path_str = await asyncio.to_thread(
            _write_temp, msg.audio.audio_bytes, _suffix
        )
        tmp_path = Path(tmp_path_str)

        try:
            result = await asyncio.wait_for(
                self._hub._stt.transcribe(tmp_path),
                timeout=timeout_s,
            )
        except asyncio.TimeoutError:
            tmp_path.unlink(missing_ok=True)
            log.warning("STT transcription timed out for msg id=%s", msg.id)
            _failed_content = self._hub._msg_manager.get("stt_failed")
            await self._hub.dispatch_response(
                self._build_stt_reply(msg, reply=True),
                Response(content=_failed_content),
            )
            _STT_STAGE_OUTCOMES["failed"] += 1
            return msg, _DROP
        except Exception as _exc:
            from lyra.stt import STTUnavailableError

            tmp_path.unlink(missing_ok=True)
            if isinstance(_exc, STTUnavailableError):
                log.warning(
                    "STT adapter unavailable for msg id=%s: %s", msg.id, _exc
                )
                _unavail_content = self._hub._msg_manager.get("stt_unavailable")
                await self._hub.dispatch_response(
                    self._build_stt_reply(msg, reply=True),
                    Response(content=_unavail_content),
                )
                _STT_STAGE_OUTCOMES["unavailable"] += 1
                return msg, _DROP
            log.exception("STT transcription failed for msg id=%s", msg.id)
            _failed_content = self._hub._msg_manager.get("stt_failed")
            await self._hub.dispatch_response(
                self._build_stt_reply(msg, reply=True),
                Response(content=_failed_content),
            )
            _STT_STAGE_OUTCOMES["failed"] += 1
            return msg, _DROP
        else:
            tmp_path.unlink(missing_ok=True)

        # 5. Post-transcription guards.
        from lyra.stt import is_whisper_noise

        transcript = result.text.strip()

        if is_whisper_noise(transcript):
            log.info("STT noise for msg id=%s — replied with stt_noise", msg.id)
            _noise_content = self._hub._msg_manager.get("stt_noise")
            await self._hub.dispatch_response(
                self._build_stt_reply(msg, reply=True),
                Response(content=_noise_content),
            )
            _STT_STAGE_OUTCOMES["noise"] += 1
            return msg, _DROP

        if transcript.startswith("/"):
            log.warning(
                "msg id=%s: transcript starts with '/' — slash-command injection guard",
                msg.id,
            )
            _invalid_content = self._hub._msg_manager.get("stt_invalid")
            await self._hub.dispatch_response(
                self._build_stt_reply(msg, reply=True),
                Response(content=_invalid_content),
            )
            _STT_STAGE_OUTCOMES["invalid"] += 1
            return msg, _DROP

        if len(transcript) > MAX_TRANSCRIPT_LEN:
            log.warning(
                "msg id=%s: transcript length %d exceeds cap %d — stt_invalid",
                msg.id,
                len(transcript),
                MAX_TRANSCRIPT_LEN,
            )
            _invalid_content = self._hub._msg_manager.get("stt_invalid")
            await self._hub.dispatch_response(
                self._build_stt_reply(msg, reply=True),
                Response(content=_invalid_content),
            )
            _STT_STAGE_OUTCOMES["invalid"] += 1
            return msg, _DROP

        # 6. Success — echo transcript and continue pipeline.
        await self._hub.dispatch_response(
            self._build_stt_reply(msg, reply=False),
            Response(content=f"\U0001f3a4 [voice]: {transcript}"),
        )
        updated = dataclasses.replace(
            msg,
            text=transcript,
            text_raw=f"\U0001f3a4 [voice]: {transcript}",
            language=result.language,
            audio=None,
        )
        _STT_STAGE_OUTCOMES["success"] += 1
        return updated, None

    def _validate_platform(
        self,
        msg: InboundMessage,
    ) -> PipelineResult | None:
        try:
            Platform(msg.platform)
        except ValueError:
            log.warning(
                "unknown platform %r in msg id=%s — message dropped",
                msg.platform,
                msg.id,
            )
            return _DROP
        return None

    def _check_rate_limit(
        self,
        msg: InboundMessage,
        key: RoutingKey,
    ) -> PipelineResult | None:
        if self._hub._is_rate_limited(msg):
            log.warning(
                "rate limit exceeded for %s — message dropped",
                key,
            )
            return _DROP
        return None

    def _resolve_binding(
        self,
        msg: InboundMessage,
        key: RoutingKey,
    ) -> Binding | None:
        binding = self._hub.resolve_binding(msg)
        if binding is None:
            log.warning(
                "unmatched routing key %s — message dropped",
                key,
            )
        return binding

    def _lookup_agent(
        self,
        binding: Binding,
        key: RoutingKey,
    ) -> AgentBase | None:
        agent = self._hub.agent_registry.get(binding.agent_name)
        if agent is None:
            log.warning(
                "no agent registered for %r (routing %s) — message dropped",
                binding.agent_name,
                key,
            )
        return agent

    async def _dispatch_command(
        self,
        msg: InboundMessage,
        router: Any,
        pool: Pool,
        key: RoutingKey,
    ) -> PipelineResult:
        # Wire callbacks before dispatch — /clear needs a live _session_reset_fn.
        _agent = self._hub.agent_registry.get(pool.agent_name)
        if _agent is not None and hasattr(_agent, "configure_pool"):
            _agent.configure_pool(pool)
        try:
            response = await router.dispatch(msg, pool)
        except Exception as exc:
            log.exception(
                "command dispatch failed for %s: %s",
                key,
                exc,
            )
            _content = (
                self._hub._msg_manager.get("generic")
                if self._hub._msg_manager
                else GENERIC_ERROR_REPLY
            )
            response = Response(content=_content)
            self._trace(
                "outbound",
                "command_error",
                action=Action.COMMAND_HANDLED.value,
            )
            return PipelineResult(action=Action.COMMAND_HANDLED, response=response)

        if response is None:  # !-prefixed command not found — fall through to pool
            self._trace("processor", "command_fallthrough")
            return await self._submit_to_pool(msg, pool, key)

        self._trace("outbound", "command_handled", action=Action.COMMAND_HANDLED.value)
        return PipelineResult(
            action=Action.COMMAND_HANDLED,
            response=response,
        )

    async def _resolve_context(  # noqa: C901 — three resume paths with guard checks
        self, msg: InboundMessage, pool: Pool, pool_id: str
    ) -> ResumeStatus:
        """Attempt session resume before pool.submit().

        Three paths (priority order): (1) reply-to-resume, (2) thread-session-resume,
        (3) last-active-session from TurnStore.

        Returns:
            RESUMED  — a session was successfully resumed via any path.
            FRESH    — Path 2 was attempted but rejected (session pruned /
                       invalid / expired); Claude will start fresh. The caller
                       should notify the user.
            SKIPPED  — no resume was attempted (pool busy, group chat, first
                       use, no TurnStore, …). Silent and expected.
        """
        path2_attempted = False

        # Path 1: reply-to-resume via MessageIndex (#341).
        if msg.reply_to_id is not None and self._hub._message_index is None:
            log.debug("reply-to-resume: no MessageIndex configured — skipping")
        if msg.reply_to_id is not None and self._hub._message_index is not None:
            session_id = await self._hub._message_index.resolve(
                pool_id, str(msg.reply_to_id)
            )
            if session_id is not None:
                if not pool.is_idle:
                    log.info(
                        "reply-to-resume: pool %r busy — skipping resume of session %r",
                        pool_id,
                        session_id,
                    )
                else:
                    log.info(
                        "reply-to-resume: resuming session %r for pool %r",
                        session_id,
                        pool_id,
                    )
                    accepted = await pool.resume_session(session_id)
                    if accepted and self._hub._turn_store is not None:
                        await self._hub._turn_store.increment_resume_count(session_id)
                    return ResumeStatus.RESUMED

        # Path 2: thread-session-resume.
        thread_session_id: str | None = msg.platform_meta.get("thread_session_id")
        if thread_session_id is not None:
            # Scope-validate: session must belong to this pool (#525).
            if self._hub._turn_store is not None:
                session_pool = await self._hub._turn_store.get_session_pool_id(
                    thread_session_id
                )
                if session_pool is None or session_pool != pool_id:
                    log.warning(
                        "thread-session-resume: scope mismatch for %r — "
                        "expected pool %r, got %r — skipping",
                        thread_session_id,
                        pool_id,
                        session_pool,
                    )
                    return ResumeStatus.SKIPPED
            else:
                # No TurnStore → cannot validate scope → safe default: skip.
                log.debug(
                    "thread-session-resume: no TurnStore — skipping %r",
                    thread_session_id,
                )
                return ResumeStatus.SKIPPED
            if not pool.is_idle:
                log.info(
                    "thread-session-resume: pool %r busy — skipping %r",
                    pool_id,
                    thread_session_id,
                )
                return ResumeStatus.SKIPPED
            log.info(
                "thread-session-resume: resuming %r for pool %r",
                thread_session_id,
                pool_id,
            )
            path2_attempted = True
            accepted = await pool.resume_session(thread_session_id)
            if accepted:
                await self._hub._turn_store.increment_resume_count(thread_session_id)
                return ResumeStatus.RESUMED
            log.info(
                "thread-session-resume: session %r not accepted"
                " — falling through to Path 3",
                thread_session_id,
            )

        # Path 3: last-active-session.
        # Group-chat is_group guards removed (#356) — scope_id is now user-scoped
        # in shared spaces, so each user has their own pool by construction.
        if pool.is_idle and self._hub._turn_store is not None:
            last_sid = await self._hub._turn_store.get_last_session(pool_id)
            if last_sid is None:
                log.debug(
                    "last-session-resume: no prior session for pool %r",
                    pool_id,
                )
            elif last_sid == pool.session_id:
                # Session IDs match — but verify the session is still viable.
                _agent = self._hub.agent_registry.get(pool.agent_name)
                _alive = (
                    _agent.is_backend_alive(pool.pool_id)
                    if _agent is not None
                    else True  # assume alive for non-CLI agents
                )
                if _alive:
                    log.debug(
                        "last-session-resume: pool %r already on session %r",
                        pool_id,
                        last_sid,
                    )
                    return ResumeStatus.SKIPPED
                log.warning(
                    "last-session-resume: pool %r session %r matches"
                    " but backend is dead — skipping guard",
                    pool_id,
                    last_sid,
                )
                # Fall through: backend is dead — do not treat as "already on session".
                # The pool will start fresh on the next send() call.
            else:
                log.info(
                    "last-session-resume: resuming %r for pool %r",
                    last_sid,
                    pool_id,
                )
                accepted = await pool.resume_session(last_sid)
                if accepted:
                    await self._hub._turn_store.increment_resume_count(last_sid)
                return ResumeStatus.RESUMED

        return ResumeStatus.FRESH if path2_attempted else ResumeStatus.SKIPPED

    async def _submit_to_pool(
        self,
        msg: InboundMessage,
        pool: Pool,
        key: RoutingKey,
    ) -> PipelineResult:
        if (key.platform, msg.bot_id) not in self._hub.adapter_registry:
            log.error(
                "no adapter registered for (%s, %s) — response dropped",
                msg.platform,
                msg.bot_id,
            )
            self._trace(
                "outbound",
                "no_adapter",
                platform=msg.platform,
                bot_id=msg.bot_id,
                action=Action.DROP.value,
            )
            return _DROP
        if await self._hub.circuit_breaker_drop(msg):
            self._trace("outbound", "circuit_open", action=Action.DROP.value)
            return _DROP
        # Register session persistence callback once.
        _update_fn = msg.platform_meta.get("_session_update_fn")
        if _update_fn is not None and pool._observer._session_update_fn is None:
            pool._observer.register_session_update_fn(_update_fn)
        # Wire provider callbacks before _resolve_context (first message after restart).
        _agent = self._hub.agent_registry.get(pool.agent_name)
        if _agent is not None and hasattr(_agent, "configure_pool"):
            _agent.configure_pool(pool)
        try:
            status = await self._resolve_context(msg, pool, pool.pool_id)
        except Exception:
            log.warning(
                "_resolve_context failed — continuing with active session",
                exc_info=True,
            )
            status = ResumeStatus.SKIPPED
        self._trace(
            "outbound",
            "message_submitted",
            adapter=msg.platform,
            resume_status=status.value,
        )
        if status == ResumeStatus.FRESH:
            await self._notify_session_fallthrough(msg)
        return PipelineResult(action=Action.SUBMIT_TO_POOL, pool=pool)

    async def _notify_session_fallthrough(self, msg: InboundMessage) -> None:
        """Send a pre-response notice when Path 2 resume fails and Claude starts fresh.

        Uses try_notify_user so any send failure is logged and swallowed —
        the pool submit always proceeds regardless.
        """
        from .outbound_errors import try_notify_user

        try:
            platform = Platform(msg.platform)
        except ValueError:
            return
        adapter = self._hub.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            return
        circuit = (
            self._hub.circuit_registry.get(msg.platform)
            if self._hub.circuit_registry is not None
            else None
        )
        await try_notify_user(
            msg.platform, adapter, msg, _SESSION_FALLTHROUGH_MSG, circuit=circuit
        )
