"""Register resolved agents on the hub."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from lyra.bootstrap.factory.agent_factory import ResolveAgentsDeps, _resolve_agents
from lyra.bootstrap.factory.config import _load_llm_config
from lyra.core.agent import Agent
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli.cli_pool import CliPool
from lyra.core.hub import Hub
from lyra.core.messaging.messages import MessageManager
from lyra.core.ports.stt import STTProtocol
from lyra.core.ports.tts import TtsProtocol
from lyra.infrastructure.stores.agent_store import AgentStore

if TYPE_CHECKING:
    from lyra.llm.llm_client import LlmClient

log = logging.getLogger(__name__)


def register_agents(  # noqa: PLR0913 — registration requires all deps
    hub: Hub,
    agent_configs: dict[str, Agent],
    cli_pool: CliPool | None,
    circuit_registry: CircuitRegistry,
    msg_manager: MessageManager,
    stt_service: STTProtocol | None,
    tts_service: TtsProtocol | None,
    agent_store: AgentStore | None,
    raw_config: dict,
    nats_llm_client: "LlmClient | None",
    *,
    cli_nats_driver: "LlmClient | None" = None,
) -> None:
    """Resolve agents from configs and register them on the hub."""
    llm_cfg = _load_llm_config(raw_config)
    all_agents = _resolve_agents(
        ResolveAgentsDeps(
            agent_configs=agent_configs,
            cli_pool=cli_pool,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            stt_service=stt_service,
            tts_service=tts_service,
            agent_store=agent_store,
            llm_cfg=llm_cfg,
            nats_llm_client=nats_llm_client,
            cli_nats_driver=cli_nats_driver,
        )
    )
    for ag in all_agents.values():
        hub.register_agent(ag)
