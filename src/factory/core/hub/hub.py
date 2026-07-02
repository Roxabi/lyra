from __future__ import annotations

import asyncio
import logging
import time
from typing import TYPE_CHECKING, cast

from ..auth.authenticator import Authenticator
from ..auth.identity import Identity
from ..config import HubConfig, PoolConfig
from ..messaging.bus import Bus
from ..messaging.inbound_bus import LocalBus
from ..messaging.message import InboundMessage, Platform
from ..pool import Pool
from ..tts_dispatch import AudioPipeline
from .hub_circuit_breaker import HubCircuitBreakerMixin
from .hub_dispatch import HubDispatchMixin
from .hub_pool_delegation import HubPoolDelegationMixin
from .hub_protocol import (  # noqa: F401 — DEBT:re-export-init — public re-export
    Binding,
    ChannelAdapter,
    RoutingKey,
)
from .hub_rate_limit import RateLimiter
from .hub_registration import HubRegistrationMixin
from .hub_shutdown import HubShutdownMixin
from .identity_resolver import IdentityResolver
from .middleware import build_default_pipeline
from .outbound import OutboundRouter, OutboundRouterDeps
from .pipeline import PoolManager

if TYPE_CHECKING:
    from collections import deque

    from factory.core.ports.blobstore import BlobStorePort
    from factory.infrastructure.stores.identity.identity_alias_store import (
        IdentityAliasStore,
    )
    from factory.infrastructure.stores.identity.pairing import PairingManager
    from factory.infrastructure.stores.registry.prefs_store import PrefsStore
    from factory.transport.turn_publisher import TurnPublisher
    from factory.transport.typing_publisher import TypingPublisher

    from ..agent import AgentBase
    from ..auth.agent_grants import AgentAuthorizer
    from ..cli.cli_pool import CliPool
    from ..lifecycle.circuit_breaker import CircuitRegistry
    from ..memory import MemoryManager
    from ..messaging.messages import MessageManager
    from ..ports.active_jobs import ActiveJobsRecorder
    from ..ports.resume_publisher import ResumePublisherPort
    from ..ports.stt import STTProtocol
    from ..ports.tts import TtsProtocol
    from ..stores import MessageIndexProtocol, TurnStoreProtocol
    from .event_bus import PipelineEventBus
    from .outbound import OutboundDispatcher

log = logging.getLogger(__name__)


class Hub(
    HubShutdownMixin,
    HubCircuitBreakerMixin,
    HubPoolDelegationMixin,
    HubDispatchMixin,
    HubRegistrationMixin,
):
    """Central hub: Bus + OutboundDispatchers + adapter registry + pools."""

    BUS_SIZE = 100  # const-ok: named constant definition — canonical bus queue depth

    def __init__(  # noqa: PLR0913, PLR0915 — DEBT:wiring-bootstrap-deps
        self,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        pairing_manager: "PairingManager | None" = None,
        stt: "STTProtocol | None" = None,
        tts: "TtsProtocol | None" = None,
        prefs_store: "PrefsStore | None" = None,
        event_bus: "PipelineEventBus | None" = None,
        inbound_bus: "Bus[InboundMessage] | None" = None,
        config: HubConfig | None = None,
        resume_publisher: "ResumePublisherPort | None" = None,
        authorizer: "AgentAuthorizer | None" = None,
    ) -> None:
        cfg = config if config is not None else HubConfig()
        if cfg.max_pools <= 0:
            raise ValueError(f"max_pools must be > 0, got {cfg.max_pools}")
        self._platform_queue_maxsize = cfg.platform_queue_maxsize
        self.inbound_bus: Bus[InboundMessage] = inbound_bus or LocalBus(
            name="inbound",
            staging_maxsize=cfg.staging_maxsize,
            queue_depth_threshold=cfg.queue_depth_threshold,
        )
        self.outbound_dispatchers: dict[tuple[Platform, str], OutboundDispatcher] = {}
        self.adapter_registry: dict[tuple[Platform, str], ChannelAdapter] = {}
        self.agent_registry: dict[str, AgentBase] = {}
        self.bindings: dict[RoutingKey, Binding] = {}
        self.circuit_registry: CircuitRegistry | None = circuit_registry
        self._msg_manager: MessageManager | None = msg_manager
        if msg_manager is None:
            log.debug(
                "Hub initialised without a MessageManager"
                " — STT error replies will use hardcoded fallbacks"
            )
        self._pairing_manager = pairing_manager
        self._message_index: MessageIndexProtocol | None = None
        self._stt: STTProtocol | None = stt
        self._tts_value: TtsProtocol | None = tts
        self._socialmedia_client = None
        self._pool_ttl = cfg.pool_ttl
        self._debounce_ms = cfg.debounce_ms
        self._cancel_on_new_message = cfg.cancel_on_new_message
        self._rate_limiter = RateLimiter(cfg.rate_limit, cfg.rate_window)
        self._start_time: float = time.monotonic()
        self._memory: MemoryManager | None = None
        self._memory_tasks: set[asyncio.Task] = set()
        self._turn_store: TurnStoreProtocol | None = None
        self._turn_publisher: TurnPublisher | None = None
        self._resume_publisher: ResumePublisherPort | None = resume_publisher
        self._authorizer: AgentAuthorizer | None = authorizer
        # T1 — typing-plane publisher; wired by bootstrap, consumed by T2.
        self._typing_publisher: TypingPublisher | None = None
        self._turn_timeout = cfg.turn_timeout
        self._prefs_store: PrefsStore | None = prefs_store
        self._safe_dispatch_timeout = cfg.safe_dispatch_timeout
        self._max_merged_chars = cfg.max_merged_chars
        self._max_pools = cfg.max_pools
        self.cli_pool: CliPool | None = None
        self._event_bus: PipelineEventBus | None = event_bus
        self._pool_config = PoolConfig(
            turn_timeout=cfg.turn_timeout,
            debounce_ms=cfg.debounce_ms,
            safe_dispatch_timeout=cfg.safe_dispatch_timeout,
            max_merged_chars=cfg.max_merged_chars,
            cancel_on_new_message=cfg.cancel_on_new_message,
        )
        self._pool_manager = PoolManager(self, self._pool_config)
        self._audio_pipeline = AudioPipeline(self)
        self._authenticators: dict[tuple[Platform, str], Authenticator] = {}
        self._alias_store: IdentityAliasStore | None = None
        # Populated by dashboard RPC heartbeat subscribers (#1771).
        self._dashboard_worker_freshness: dict[str, float] = {}
        # Wired by hub_standalone for dashboard job catalog (#1772).
        self._active_jobs_store: object | None = None
        self._active_jobs_coord: object | None = None
        # Dashboard agent/soul RPC (#1760).
        self._agent_store: object | None = None
        self._user_store: object | None = None
        self._blob_store: BlobStorePort | None = None
        # Wired by fleet_ingest for /fleet dashboard RPC.
        self._fleet_store: object | None = None
        self._pipeline_store: object | None = None
        self._identity_resolver = IdentityResolver(
            authenticators=self._authenticators,
            bindings=self.bindings,
        )
        self._outbound_router = OutboundRouter(
            OutboundRouterDeps(
                adapters=self.adapter_registry,
                dispatchers=self.outbound_dispatchers,
                audio_pipeline=self._audio_pipeline,
                circuit_registry=self.circuit_registry,
                msg_manager=self._msg_manager,
                tts=self._tts,
                memory_tasks=self._memory_tasks,
            )
        )

    @property
    def _last_processed_at(self) -> float | None:
        return self._outbound_router.last_processed_at

    @property
    def _tts(self) -> "TtsProtocol | None":
        return self._tts_value

    @_tts.setter
    def _tts(self, value: "TtsProtocol | None") -> None:
        self._tts_value = value
        self._outbound_router.set_tts(value)

    @property
    def pools(self) -> dict[str, Pool]:
        return self._pool_manager.pools

    def resolve_identity(
        self, user_id: str | None, platform: str, bot_id: str
    ) -> Identity:
        """Resolve identity for a user on a given (platform, bot_id)."""
        return self._identity_resolver.resolve_identity(user_id, platform, bot_id)

    def _resolve_message_trust(self, msg: InboundMessage) -> InboundMessage:
        """Re-resolve trust level on the Hub side (C3 — trust re-resolution)."""
        return self._identity_resolver.resolve_message_trust(msg)

    def resolve_binding(self, msg: InboundMessage) -> Binding | None:
        """Resolve binding: exact key, then wildcard fallback, else None."""
        return self._identity_resolver.resolve_binding(msg)

    def get_agent(self, name: str) -> AgentBase | None:
        return self.agent_registry.get(name)

    def active_jobs_recorder(self) -> ActiveJobsRecorder | None:
        """Active-jobs registry recorder (None until ``hub_standalone`` wires it).

        Backs ``PoolContext``: the Pool records a run open/close here so the
        dashboard live view (#1772) reflects in-flight jobs.  Typed via the
        narrow ``ActiveJobsRecorder`` port; the concrete instance is a
        ``RegistryCoordinator`` set on ``_active_jobs_coord`` at hub startup.
        """
        return cast("ActiveJobsRecorder | None", self._active_jobs_coord)

    def get_message(self, key: str, **kwargs: str) -> str | None:
        return self._msg_manager.get(key, **kwargs) if self._msg_manager else None

    @property
    def _rate_timestamps(self) -> dict[tuple[str, str, str], deque[float]]:
        return self._rate_limiter._timestamps

    @property
    def _rate_limit(self) -> int:
        return self._rate_limiter._limit

    @property
    def _rate_window(self) -> int:
        return self._rate_limiter._window

    def _is_rate_limited_by_key(self, key: tuple[str, str, str]) -> bool:
        return self._rate_limiter.is_limited_by_key(key)

    def _is_rate_limited(self, msg: InboundMessage) -> bool:
        return self._rate_limiter.is_limited(msg)

    async def run(self) -> None:
        """Hub bus consumer loop. Runs until cancelled."""
        pipeline = build_default_pipeline(
            self,
            authorizer=self._authorizer,
            resume_publisher=self._resume_publisher,
            event_bus=self._event_bus,
        )
        while True:
            msg = await self.inbound_bus.get()
            try:
                result = await pipeline.process(msg)
                await self._dispatch_pipeline_result(msg, result)
            except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: hub-loop — pipeline/dispatch failure must not crash consumer
                log.exception("hub message handling failed for msg id=%s", msg.id)
            finally:
                self.inbound_bus.task_done()
