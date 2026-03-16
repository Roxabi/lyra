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


@dataclass(frozen=True)
class PipelineResult:
    """Immutable result from MessagePipeline.process()."""

    action: Action
    response: Response | None = None
    pool: Pool | None = None


_DROP = PipelineResult(action=Action.DROP)


class MessagePipeline:
    """Fail-fast message routing pipeline extracted from Hub.run().

    Each guard stage returns ``PipelineResult`` to stop processing or
    ``None`` to continue to the next stage. Terminal stages always
    return a ``PipelineResult``.
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

        # Parse command prefix and attach CommandContext to the message
        cmd_ctx = _command_parser.parse(msg.text)
        if cmd_ctx is not None:
            msg = dataclasses.replace(msg, command=cmd_ctx)

        # Rewrite bare URLs to /add before command detection (issue #99)
        if router and hasattr(router, "prepare"):
            msg = router.prepare(msg)

        if router and router.is_command(msg):
            return await self._dispatch_command(
                msg,
                router,
                pool,
                key,
            )

        return await self._submit_to_pool(msg, pool, key)

    # -- guard stages (return None to continue) ---

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
        """Return resolved binding, or None (with log) to drop."""
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
        """Return agent, or None (with log) to drop."""
        agent = self._hub.agent_registry.get(binding.agent_name)
        if agent is None:
            log.warning(
                "no agent registered for %r (routing %s) — message dropped",
                binding.agent_name,
                key,
            )
        return agent

    # -- terminal stages ---

    async def _dispatch_command(
        self,
        msg: InboundMessage,
        router: Any,
        pool: Pool,
        key: RoutingKey,
    ) -> PipelineResult:
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

        if response is None:
            # !-prefixed command not found — fall through to pool submission
            return await self._submit_to_pool(msg, pool, key)

        return PipelineResult(
            action=Action.COMMAND_HANDLED,
            response=response,
        )

    async def _resolve_context(
        self, msg: InboundMessage, pool: Pool, pool_id: str
    ) -> None:
        """Attempt session resume before pool.submit(). No-op on any failure.

        Two resume paths (in priority order):
        1. Reply-to-resume: message replies to a known assistant turn — look up
           the session via ContextResolver and resume it.
        2. Thread-session-resume: Discord thread with a stored session_id from
           a previous conversation — resume via platform_meta["thread_session_id"].

        # Note: pool._session_resume_fn is wired lazily by
        # SimpleAgent._maybe_register_resume on the first process() call.
        # If called before any process(), resume_session() is a no-op
        # (fn is None). In practice, pools exist only after agent processing
        # begins.
        """
        # Path 1: reply-to-resume (existing logic — Telegram and Discord).
        if msg.reply_to_id is not None:
            resolver = self._hub._context_resolver
            if resolver is not None:
                resolved = await resolver.resolve(msg.reply_to_id)
                if resolved is not None:
                    if resolved.pool_id != pool_id:
                        log.info(
                            "reply-to-resume: cross-pool mismatch"
                            " (resolved=%r current=%r) — skipping",
                            resolved.pool_id,
                            pool_id,
                        )
                    # TODO(post-#67): replace with user_id ownership check once
                    # conversation_turns stores user_id. For now, restrict resume to
                    # private chats to prevent cross-user session access in groups.
                    elif msg.platform_meta.get("is_group"):
                        log.info(
                            "reply-to-resume: group chat"
                            " — skipping resume (cross-user risk)",
                        )
                    elif not pool.is_idle:
                        log.info(
                            "reply-to-resume: pool %r busy"
                            " — skipping resume of session %r",
                            pool_id,
                            resolved.session_id,
                        )
                    else:
                        log.info(
                            "reply-to-resume: resuming session %r for pool %r",
                            resolved.session_id,
                            pool_id,
                        )
                        await pool.resume_session(resolved.session_id)
                        return  # reply-to-resume took priority; skip thread resume

        # Path 2: thread-session-resume (Discord owned threads across restarts).
        thread_session_id: str | None = msg.platform_meta.get("thread_session_id")
        if thread_session_id is not None:
            if not pool.is_idle:
                log.info(
                    "thread-session-resume: pool %r busy"
                    " — skipping resume of session %r",
                    pool_id,
                    thread_session_id,
                )
                return
            log.info(
                "thread-session-resume: resuming session %r for pool %r",
                thread_session_id,
                pool_id,
            )
            await pool.resume_session(thread_session_id)

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
        # Register session persistence callback if provided by the adapter (write-side
        # fix for Discord threads). Only set once — guards against repeated messages
        # in the same thread overwriting the callback unnecessarily.
        _update_fn = msg.platform_meta.get("_session_update_fn")
        if _update_fn is not None and pool._observer._session_update_fn is None:
            pool._observer.register_session_update_fn(_update_fn)
        try:
            await self._resolve_context(msg, pool, pool.pool_id)
        except Exception:
            log.warning(
                "reply-to-resume: _resolve_context failed"
                " — continuing with active session",
                exc_info=True,
            )
        return PipelineResult(
            action=Action.SUBMIT_TO_POOL,
            pool=pool,
        )
