"""
SimpleAgent — first concrete AgentBase implementation.

Wraps CliPool to route messages through a persistent Claude CLI process.
Model and backend are read from the agent's TOML config (ModelConfig),
not hardcoded here.
"""

from __future__ import annotations

import logging
from typing import Any

from lyra.core.agent import Agent, AgentBase
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.cli_pool import CliPool, CliResult
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    Message,
    Response,
    extract_text,
)
from lyra.core.messages import MessageManager
from lyra.core.pool import Pool

log = logging.getLogger(__name__)


class SimpleAgent(AgentBase):
    """Agent that routes every message through a persistent Claude CLI process.

    One CliPool instance is shared across all SimpleAgent instances —
    pass it in from the hub so it can be stopped cleanly on shutdown.

    Wiring (in main.py / hub bootstrap)::

        cli_pool = CliPool()
        await cli_pool.start()

        agent_config = load_agent_config("lyra_default")
        agent = SimpleAgent(agent_config, cli_pool)
        hub.register_agent(agent)
    """

    def __init__(
        self,
        config: Agent,
        cli_pool: CliPool,
        circuit_registry: CircuitRegistry | None = None,
        admin_user_ids: set[str] | None = None,
        msg_manager: MessageManager | None = None,
    ) -> None:
        super().__init__(
            config,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
        )
        self._pool = cli_pool

    async def process(self, msg: Message, pool: Pool) -> Response:
        self._maybe_reload()
        text = extract_text(msg)
        model_cfg = self.config.model_config

        log.debug(
            "[agent:%s][pool:%s] processing message (%d chars)",
            self.name,
            pool.pool_id,
            len(text),
        )

        result: CliResult = await self._pool.send(
            pool.pool_id, text, model_cfg, system_prompt=self.config.system_prompt
        )

        if not result.ok:
            log.warning(
                "[agent:%s][pool:%s] CLI error: %s",
                self.name,
                pool.pool_id,
                result.error,
            )
            # Timeout gets a specific message; all other errors get a generic one
            if "Timeout" in result.error:
                user_msg = "Response timed out. Please try again."
            else:
                user_msg = (
                    self._msg_manager.get("generic")
                    if self._msg_manager
                    else GENERIC_ERROR_REPLY
                )
            return Response(
                content=user_msg,
                metadata={"error": True},
            )

        reply = result.result
        meta: dict[str, Any] = {"session_id": result.session_id}
        if result.warning:
            meta["warning"] = result.warning

        if not reply:
            log.warning(
                "[agent:%s][pool:%s] empty reply from CLI",
                self.name,
                pool.pool_id,
            )

        return Response(content=reply, metadata=meta)
