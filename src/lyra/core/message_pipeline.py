"""Fail-fast message routing pipeline extracted from Hub.run()."""

from __future__ import annotations

import dataclasses
import enum
import logging
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .agent import AgentBase
from .command_parser import CommandParser
from .message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    Platform,
    Response,
)
from .pool import Pool

if TYPE_CHECKING:
    from .hub import Binding, Hub, RoutingKey

log = logging.getLogger(__name__)

_command_parser = CommandParser()


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
    """

    def __init__(self, hub: Hub) -> None:
        self._hub = hub

    async def process(
        self,
        msg: InboundMessage,
    ) -> PipelineResult:
        """Route *msg* through the pipeline stages."""
        from .hub import RoutingKey

        result = self._validate_platform(msg)
        if result is not None:
            return result

        key = RoutingKey(
            Platform(msg.platform),
            msg.bot_id,
            msg.scope_id,
        )

        result = self._check_rate_limit(msg, key)
        if result is not None:
            return result

        binding = self._resolve_binding(msg, key)
        if binding is None:
            return _DROP

        agent = self._lookup_agent(binding, key)
        if agent is None:
            return _DROP

        pool = self._hub.get_or_create_pool(
            binding.pool_id,
            binding.agent_name,
        )
        router = getattr(agent, "command_router", None)

        cmd_ctx = _command_parser.parse(msg.text)
        if cmd_ctx is not None:
            msg = dataclasses.replace(msg, command=cmd_ctx)

        # Rewrite bare URLs to /add before command detection (#99).
        if router and hasattr(router, "prepare"):
            msg = router.prepare(msg)

        if router and router.is_command(msg):
            return await self._dispatch_command(msg, router, pool, key)

        return await self._submit_to_pool(msg, pool, key)

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
            return PipelineResult(action=Action.COMMAND_HANDLED, response=response)

        if response is None:  # !-prefixed command not found — fall through to pool
            return await self._submit_to_pool(msg, pool, key)

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
                if msg.platform_meta.get("is_group"):
                    log.info(
                        "reply-to-resume: group chat"
                        " — skipping resume (cross-user risk)",
                    )
                elif not pool.is_idle:
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
                    await pool.resume_session(session_id)
                    return ResumeStatus.RESUMED

        # Path 2: thread-session-resume.
        thread_session_id: str | None = msg.platform_meta.get("thread_session_id")
        if thread_session_id is not None:
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
                return ResumeStatus.RESUMED
            log.info(
                "thread-session-resume: session %r not accepted"
                " — falling through to Path 3",
                thread_session_id,
            )

        # Path 3: last-active-session — skip group chats (cross-user risk).
        if msg.platform_meta.get("is_group"):
            return ResumeStatus.FRESH if path2_attempted else ResumeStatus.SKIPPED
        if pool.is_idle and self._hub._turn_store is not None:
            last_sid = await self._hub._turn_store.get_last_session(pool_id)
            if last_sid is None:
                log.debug(
                    "last-session-resume: no prior session for pool %r",
                    pool_id,
                )
            elif last_sid == pool.session_id:
                log.debug(
                    "last-session-resume: pool %r already on session %r",
                    pool_id,
                    last_sid,
                )
            else:
                log.info(
                    "last-session-resume: resuming %r for pool %r",
                    last_sid,
                    pool_id,
                )
                await pool.resume_session(last_sid)
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
            return _DROP
        if await self._hub.circuit_breaker_drop(msg):
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
