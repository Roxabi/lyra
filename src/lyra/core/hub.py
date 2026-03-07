from __future__ import annotations

import asyncio
import collections.abc
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import NamedTuple, Protocol

from .agent import AgentBase
from .message import GENERIC_ERROR_REPLY, Message, Platform, Response
from .pool import Pool

log = logging.getLogger(__name__)


class ChannelAdapter(Protocol):
    """Interface every channel adapter must implement.

    Security contract: adapters are responsible for verifying the identity
    of the sender (e.g. via platform token, signed webhook, or session)
    before constructing a Message. The hub trusts the user_id in the Message
    as authentic. Never set user_id from unverified inbound data.
    """

    async def send(self, original_msg: Message, response: Response) -> None: ...

    async def send_streaming(
        self, original_msg: Message, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response to the channel with edit-in-place.

        Default implementation accumulates all chunks and calls send().
        Adapters override for progressive display.
        """
        ...


class RoutingKey(NamedTuple):
    """Routing key: (platform, bot_id, user_id). Use user_id='*' for wildcard."""

    platform: Platform
    bot_id: str
    user_id: str

    def to_pool_id(self) -> str:
        """Canonical pool ID: '{platform.value}:{bot_id}:{user_id}'.

        Use this method as the single source of truth for pool ID format (ADR-001 §4).
        Never construct the pool ID string inline.
        """
        return f"{self.platform.value}:{self.bot_id}:{self.user_id}"


@dataclass(frozen=True)
class Binding:
    agent_name: str
    pool_id: str


class Hub:
    """Central hub: bounded async bus + adapter registry + bindings + pools."""

    BUS_SIZE = 100
    # Per-user sliding window: drop messages beyond this rate.
    RATE_LIMIT = 20  # max messages per user per window
    RATE_WINDOW = 60  # window size in seconds

    def __init__(
        self,
        bus_size: int = BUS_SIZE,
        rate_limit: int = RATE_LIMIT,
        rate_window: int = RATE_WINDOW,
    ) -> None:
        self.bus: asyncio.Queue[Message] = asyncio.Queue(maxsize=bus_size)
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.agent_registry: dict[str, AgentBase] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.pools: dict[str, Pool] = {}
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        # Sliding window: maps RoutingKey → deque of message timestamps.
        # Entries are removed when the deque empties (user inactive for > RATE_WINDOW)
        # to prevent unbounded dict growth.
        self._rate_timestamps: dict[RoutingKey, deque[float]] = {}

    # ------------------------------------------------------------------
    # Adapter registry
    # ------------------------------------------------------------------

    def register_agent(self, agent: AgentBase) -> None:
        """Register an agent implementation by name."""
        self.agent_registry[agent.name] = agent

    def register_adapter(
        self, platform: Platform, bot_id: str, adapter: ChannelAdapter
    ) -> None:
        """Register a channel adapter keyed by (platform, bot_id).

        The adapter is responsible for authenticating inbound messages before
        placing them on the bus. See ChannelAdapter for the security contract.
        """
        self.adapter_registry[(platform, bot_id)] = adapter

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def register_binding(
        self,
        platform: Platform,
        bot_id: str,
        user_id: str,
        agent_name: str,
        pool_id: str,
    ) -> None:
        """Map (platform, bot_id, user_id) -> (agent_name, pool_id).

        Raises ValueError if pool_id is already assigned to a different user_id
        on the same (platform, bot_id) — each pool must serve at most one user
        per (platform, bot_id) pair.
        """
        for existing_key, existing_binding in self.bindings.items():
            if (
                existing_key.platform == platform
                and existing_key.bot_id == bot_id
                and existing_key.user_id != user_id
                and existing_binding.pool_id == pool_id
            ):
                raise ValueError(
                    f"pool_id {pool_id!r} is already bound to user_id "
                    f"{existing_key.user_id!r} on {platform}:{bot_id}. "
                    "Each pool must serve at most one user per (platform, bot_id)."
                )
        self.bindings[RoutingKey(platform, bot_id, user_id)] = Binding(
            agent_name=agent_name,
            pool_id=pool_id,
        )

    def resolve_binding(self, msg: Message) -> Binding | None:
        """Resolve binding: exact key, then wildcard fallback, else None."""
        key = RoutingKey(msg.platform, msg.bot_id, msg.user_id)
        exact = self.bindings.get(key)
        if exact is not None:
            return exact
        wildcard = RoutingKey(msg.platform, msg.bot_id, "*")
        wildcard_binding = self.bindings.get(wildcard)
        if wildcard_binding is not None:
            # Synthesise a per-user pool_id from the real user_id so each user
            # gets an isolated Pool (own lock, own subprocess, own conversation).
            concrete_pool_id = RoutingKey(
                msg.platform, msg.bot_id, msg.user_id
            ).to_pool_id()
            return Binding(
                agent_name=wildcard_binding.agent_name, pool_id=concrete_pool_id
            )
        return None

    # ------------------------------------------------------------------
    # Pools
    # ------------------------------------------------------------------

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        """Return existing pool or create a new one.

        Uses dict.setdefault() — single atomic operation, closes the pre-PR TOCTOU
        TODO. Note: Pool is constructed unconditionally on every call; the allocation
        is negligible at personal-use scale.
        """
        return self.pools.setdefault(
            pool_id, Pool(pool_id=pool_id, agent_name=agent_name)
        )

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _is_rate_limited(self, msg: Message) -> bool:
        """Return True if this user has exceeded the per-window message limit.

        Uses a sliding window: tracks timestamps of recent messages and drops
        any that arrive after RATE_LIMIT messages within RATE_WINDOW seconds.
        Inactive-user entries are cleaned up when their deque empties to prevent
        unbounded dict growth.
        """
        key = RoutingKey(msg.platform, msg.bot_id, msg.user_id)
        now = time.monotonic()
        window_start = now - self._rate_window
        timestamps = self._rate_timestamps.get(key)
        if timestamps is not None:
            # Evict timestamps outside the current window
            while timestamps and timestamps[0] < window_start:
                timestamps.popleft()
            # Empty deque → user has been inactive; clean up to bound dict size
            if not timestamps:
                del self._rate_timestamps[key]
                timestamps = None
        if timestamps is not None and len(timestamps) >= self._rate_limit:
            return True
        if timestamps is None:
            timestamps = deque()
            self._rate_timestamps[key] = timestamps
        timestamps.append(now)
        return False

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    async def dispatch_response(self, msg: Message, response: Response) -> None:
        """Send response back via the originating adapter."""
        adapter = self.adapter_registry.get((msg.platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching responses."
            )
        await adapter.send(msg, response)

    async def dispatch_streaming(
        self, msg: Message, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response back via the originating adapter."""
        adapter = self.adapter_registry.get((msg.platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching responses."
            )
        if hasattr(adapter, "send_streaming"):
            await adapter.send_streaming(msg, chunks)
        else:
            # Fallback: accumulate and send as one message
            text = ""
            async for chunk in chunks:
                text += chunk
            await adapter.send(msg, Response(content=text))

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        while True:
            msg = await self.bus.get()
            try:
                key = RoutingKey(msg.platform, msg.bot_id, msg.user_id)
                if self._is_rate_limited(msg):
                    log.warning("rate limit exceeded for %s — message dropped", key)
                    continue
                binding = self.resolve_binding(msg)
                if binding is None:
                    log.warning("unmatched routing key %s — message dropped", key)
                    continue
                pool = self.get_or_create_pool(binding.pool_id, binding.agent_name)
                agent = self.agent_registry.get(binding.agent_name)
                if agent is None:
                    log.warning(
                        "no agent registered for %r (routing %s) — message dropped",
                        binding.agent_name,
                        key,
                    )
                    continue
                router = getattr(agent, "command_router", None)
                if router and router.is_command(msg):
                    try:
                        response = await router.dispatch(msg)
                    except Exception as exc:
                        log.exception("command dispatch failed for %s: %s", key, exc)
                        response = Response(content=GENERIC_ERROR_REPLY)
                    try:
                        await self.dispatch_response(msg, response)
                    except Exception as exc:
                        log.exception("dispatch_response() failed for %s: %s", key, exc)
                    continue
                # Fail fast — check adapter exists before spending LLM tokens
                if (msg.platform, msg.bot_id) not in self.adapter_registry:
                    log.error(
                        "no adapter registered for (%s, %s) — response dropped",
                        msg.platform,
                        msg.bot_id,
                    )
                    continue
                async with pool.lock:
                    result = agent.process(msg, pool)
                    if isinstance(result, collections.abc.AsyncIterator):
                        # Streaming path (AnthropicAgent)
                        try:
                            await self.dispatch_streaming(msg, result)
                        except Exception as exc:
                            log.exception(
                                "dispatch_streaming() failed for %s: %s",
                                key,
                                exc,
                            )
                    else:
                        # Non-streaming path (SimpleAgent)
                        try:
                            response = await result
                        except Exception as exc:
                            log.exception("agent.process() raised for %s: %s", key, exc)
                            response = Response(content=GENERIC_ERROR_REPLY)
                        try:
                            await self.dispatch_response(msg, response)
                        except Exception as exc:
                            log.exception(
                                "dispatch_response() failed for %s: %s",
                                key,
                                exc,
                            )
            finally:
                self.bus.task_done()
