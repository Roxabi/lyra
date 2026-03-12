from __future__ import annotations

import asyncio
import logging
import time
from collections import deque
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, NamedTuple, Protocol

from .agent import AgentBase
from .circuit_breaker import CircuitRegistry
from .inbound_bus import InboundBus
from .message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    OutboundMessage,
    Platform,
    Response,
)
from .messages import MessageManager
from .outbound_dispatcher import OutboundDispatcher
from .pool import Pool

if TYPE_CHECKING:
    from .pairing import PairingManager

log = logging.getLogger(__name__)


class ChannelAdapter(Protocol):
    """Interface every channel adapter must implement.

    Security contract: adapters are responsible for verifying the identity
    of the sender (e.g. via platform token, signed webhook, or session)
    before constructing an InboundMessage. The hub trusts ``InboundMessage.user_id``
    as the authenticated sender identity (used for rate limiting and pairing) and
    ``InboundMessage.scope_id`` as the conversation scope (used for pool routing).
    Never derive either from unverified inbound data.
    """

    def normalize(self, raw: Any) -> InboundMessage: ...

    async def send(self, original_msg: InboundMessage, outbound: OutboundMessage) -> None: ...

    async def send_streaming(
        self, original_msg: InboundMessage, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response to the channel with edit-in-place.

        Default implementation accumulates all chunks and calls send().
        Adapters override for progressive display.
        """
        ...


class RoutingKey(NamedTuple):
    """Routing key: (platform, bot_id, scope_id). Use scope_id='*' for wildcard."""

    platform: Platform
    bot_id: str
    scope_id: str

    def to_pool_id(self) -> str:
        """Canonical pool ID: '{platform.value}:{bot_id}:{scope_id}'.

        Use this method as the single source of truth for pool ID format (ADR-001 §4).
        Never construct the pool ID string inline.
        """
        return f"{self.platform.value}:{self.bot_id}:{self.scope_id}"


@dataclass(frozen=True)
class Binding:
    agent_name: str
    pool_id: str


class Hub:
    """Central hub: InboundBus + OutboundDispatchers + adapter registry + pools."""

    BUS_SIZE = 100
    # Per-user sliding window: drop messages beyond this rate.
    RATE_LIMIT = 20  # max messages per user per window
    RATE_WINDOW = 60  # window size in seconds

    def __init__(
        self,
        bus_size: int = BUS_SIZE,
        rate_limit: int = RATE_LIMIT,
        rate_window: int = RATE_WINDOW,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        pairing_manager: PairingManager | None = None,
    ) -> None:
        self._bus_size = bus_size
        self.inbound_bus: InboundBus = InboundBus()
        self.outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher] = {}
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.agent_registry: dict[str, AgentBase] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.pools: dict[str, Pool] = {}
        self.circuit_registry = circuit_registry
        self._msg_manager = msg_manager
        self._pairing_manager = pairing_manager
        self._rate_limit = rate_limit
        self._rate_window = rate_window
        # Sliding window: maps (platform.value, bot_id, user_id) → deque of timestamps.
        # Rate limiting is per-user (not per-scope) to prevent rate-limit bypass
        # by switching chats. Entries are removed when the deque empties.
        self._rate_timestamps: dict[tuple[str, str, str], deque[float]] = {}
        # Health monitoring timestamps (SC-3, issue #111)
        self._start_time: float = time.monotonic()
        self._last_processed_at: float | None = None

    @property
    def bus(self) -> asyncio.Queue[InboundMessage]:
        """Backward-compat alias for the inbound staging queue.

        New code should use ``inbound_bus.put(platform, msg)`` for per-platform
        isolation. This property gives direct access to the staging queue and is
        retained for tests that inject messages without going through a platform queue.
        """
        return self.inbound_bus._staging

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

        Auto-registers the platform with the InboundBus if this is the first
        adapter for that platform. The adapter is responsible for authenticating
        inbound messages before placing them on the bus. See ChannelAdapter for
        the security contract.
        """
        self.adapter_registry[(platform, bot_id)] = adapter
        # Register per-platform inbound queue on first adapter for this platform
        if platform not in self.inbound_bus.registered_platforms():
            self.inbound_bus.register(platform, maxsize=self._bus_size)

    def register_outbound_dispatcher(
        self, platform: Platform, bot_id: str, dispatcher: OutboundDispatcher
    ) -> None:
        """Register an OutboundDispatcher for the given (platform, bot_id).

        When registered, dispatch_response() and dispatch_streaming() route through
        the dispatcher queue instead of calling the adapter directly. The dispatcher
        owns the platform circuit breaker check.
        """
        self.outbound_dispatchers[(platform, bot_id)] = dispatcher

    # ------------------------------------------------------------------
    # Bindings
    # ------------------------------------------------------------------

    def register_binding(
        self,
        platform: Platform,
        bot_id: str,
        scope_id: str,
        agent_name: str,
        pool_id: str,
    ) -> None:
        """Map (platform, bot_id, scope_id) -> (agent_name, pool_id).

        Raises ValueError if pool_id is already assigned to a different scope_id
        on the same (platform, bot_id) — each pool must serve at most one scope
        per (platform, bot_id) pair.
        """
        for existing_key, existing_binding in self.bindings.items():
            if (
                existing_key.platform == platform
                and existing_key.bot_id == bot_id
                and existing_key.scope_id != scope_id
                and existing_binding.pool_id == pool_id
            ):
                raise ValueError(
                    f"pool_id {pool_id!r} is already bound to scope_id "
                    f"{existing_key.scope_id!r} on {platform}:{bot_id}. "
                    "Each pool must serve at most one scope per (platform, bot_id)."
                )
        self.bindings[RoutingKey(platform, bot_id, scope_id)] = Binding(
            agent_name=agent_name,
            pool_id=pool_id,
        )

    def resolve_binding(self, msg: InboundMessage) -> Binding | None:
        """Resolve binding: exact key, then wildcard fallback, else None."""
        scope = msg.scope_id
        key = RoutingKey(Platform(msg.platform), msg.bot_id, scope)
        exact = self.bindings.get(key)
        if exact is not None:
            return exact
        wildcard = RoutingKey(Platform(msg.platform), msg.bot_id, "*")
        wildcard_binding = self.bindings.get(wildcard)
        if wildcard_binding is not None:
            # Synthesise a per-scope pool_id from the message scope so each
            # conversation scope gets an isolated Pool (own lock, own subprocess,
            # own conversation).
            concrete_pool_id = RoutingKey(
                Platform(msg.platform), msg.bot_id, scope
            ).to_pool_id()
            return Binding(
                agent_name=wildcard_binding.agent_name, pool_id=concrete_pool_id
            )
        return None

    # ------------------------------------------------------------------
    # Pools
    # ------------------------------------------------------------------

    def get_or_create_pool(self, pool_id: str, agent_name: str) -> Pool:
        """Return existing pool or create a new one."""
        if pool_id not in self.pools:
            self.pools[pool_id] = Pool(pool_id=pool_id, agent_name=agent_name, hub=self)
        return self.pools[pool_id]

    # ------------------------------------------------------------------
    # Rate limiting
    # ------------------------------------------------------------------

    def _is_rate_limited(self, msg: InboundMessage) -> bool:
        """Return True if this user has exceeded the per-window message limit.

        Uses a sliding window: tracks timestamps of recent messages and drops
        any that arrive after RATE_LIMIT messages within RATE_WINDOW seconds.
        Inactive-user entries are cleaned up when their deque empties to prevent
        unbounded dict growth.
        """
        # str() normalizes platform: InboundMessage.platform is str, not Platform enum
        key = (str(msg.platform), msg.bot_id, msg.user_id)
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

    async def dispatch_response(self, msg: InboundMessage, response: Response) -> None:
        """Send response back via the originating adapter.

        Routes through the OutboundDispatcher when one is registered for the
        platform (fire-and-forget queue). Falls back to a direct adapter call
        when no dispatcher is registered (used in tests and command responses).
        """
        outbound = response.to_outbound()
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue(msg, outbound)
            self._last_processed_at = time.monotonic()
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
        if adapter is None:
            raise KeyError(
                f"No adapter registered for ({msg.platform!r}, {msg.bot_id!r}). "
                "Call register_adapter() before dispatching responses."
            )
        await adapter.send(msg, outbound)
        self._last_processed_at = time.monotonic()

    async def dispatch_streaming(
        self, msg: InboundMessage, chunks: AsyncIterator[str]
    ) -> None:
        """Stream response back via the originating adapter.

        Routes through the OutboundDispatcher when one is registered (fire-and-forget).
        Falls back to a direct adapter call when no dispatcher is registered.
        """
        platform = Platform(msg.platform)
        dispatcher = self.outbound_dispatchers.get((platform, msg.bot_id))
        if dispatcher is not None:
            dispatcher.enqueue_streaming(msg, chunks)
            self._last_processed_at = time.monotonic()
            return
        # Fallback: direct adapter call (backward compat / no dispatcher registered)
        adapter = self.adapter_registry.get((platform, msg.bot_id))
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
            await adapter.send(msg, OutboundMessage.from_text(text))
        self._last_processed_at = time.monotonic()

    # ------------------------------------------------------------------
    # Run loop
    # ------------------------------------------------------------------

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        while True:
            msg = await self.inbound_bus.get()
            try:
                scope = msg.scope_id
                try:
                    platform_enum = Platform(msg.platform)
                except ValueError:
                    log.warning(
                        "unknown platform %r in msg id=%s — message dropped",
                        msg.platform,
                        msg.id,
                    )
                    continue
                key = RoutingKey(platform_enum, msg.bot_id, scope)
                if self._is_rate_limited(msg):
                    log.warning("rate limit exceeded for %s — message dropped", key)
                    continue
                binding = self.resolve_binding(msg)
                if binding is None:
                    log.warning("unmatched routing key %s — message dropped", key)
                    continue
                agent = self.agent_registry.get(binding.agent_name)
                if agent is None:
                    log.warning(
                        "no agent registered for %r (routing %s) — message dropped",
                        binding.agent_name,
                        key,
                    )
                    continue
                pool = self.get_or_create_pool(binding.pool_id, binding.agent_name)
                router = getattr(agent, "command_router", None)

                # Pairing gate: runs after binding resolution, before command dispatch.
                if self._pairing_manager and self._pairing_manager.config.enabled:
                    # Allow /join through so unpaired users can pair themselves.
                    # Use router.get_command_name() as single source of truth (W4).
                    _cmd_name = (
                        router.get_command_name(msg) if router is not None else None
                    )
                    is_join_cmd = _cmd_name == "/join"
                    if not is_join_cmd:
                        paired = await self._pairing_manager.is_paired(msg.user_id)
                        if not paired:
                            if _is_group_message(msg):
                                # Groups: silently drop to avoid spamming channels.
                                log.debug(
                                    "unpaired user %s in group — message dropped", key
                                )
                                continue
                            # DMs: send a rejection message.
                            rejection = Response(
                                content="You are not paired. Use /join <CODE> to pair."
                            )
                            try:
                                await self.dispatch_response(msg, rejection)
                            except Exception:
                                pass
                            continue

                if router and router.is_command(msg):
                    try:
                        response = await router.dispatch(msg, pool)
                    except Exception as exc:
                        log.exception("command dispatch failed for %s: %s", key, exc)
                        _content = (
                            self._msg_manager.get("generic")
                            if self._msg_manager
                            else GENERIC_ERROR_REPLY
                        )
                        response = Response(content=_content)
                    try:
                        if response.content:
                            await self.dispatch_response(msg, response)
                    except Exception as exc:
                        log.exception("dispatch_response() failed for %s: %s", key, exc)
                    continue
                # Fail fast — check adapter exists before spending LLM tokens
                if (Platform(msg.platform), msg.bot_id) not in self.adapter_registry:
                    log.error(
                        "no adapter registered for (%s, %s) — response dropped",
                        msg.platform,
                        msg.bot_id,
                    )
                    continue
                # Anthropic circuit pre-process check
                # (Option B: hub-level check before agent.process())
                if self.circuit_registry is not None:
                    cb = self.circuit_registry.get("anthropic")
                    if cb is not None and cb.is_open():
                        status = cb.get_status()
                        retry_secs = int(status.retry_after or 0)
                        _retry_str = str(retry_secs)
                        _unavail = (
                            self._msg_manager.get("unavailable", retry_secs=_retry_str)
                            if self._msg_manager
                            else (
                                "Lyra is currently unavailable. "
                                f"Please try again in {retry_secs}s."
                            )
                        )
                        reply = Response(content=_unavail)
                        try:
                            await self.dispatch_response(msg, reply)
                        except Exception as exc:
                            log.exception(
                                "dispatch_response failed for fast-fail reply: %s",
                                exc,
                            )
                        continue
                # Submit to pool — non-blocking; processing + dispatch happen
                # in Pool._process_loop task
                pool.submit(msg)
            finally:
                self.inbound_bus.task_done()


# ---------------------------------------------------------------------------
# Module-level helpers for the pairing gate
# ---------------------------------------------------------------------------


def _is_group_message(msg: InboundMessage) -> bool:
    """Return True if the message originated from a group/guild channel."""
    if msg.platform == Platform.TELEGRAM.value:
        return bool(msg.platform_meta.get("is_group", False))
    if msg.platform == Platform.DISCORD.value:
        return msg.platform_meta.get("guild_id") is not None
    return False
