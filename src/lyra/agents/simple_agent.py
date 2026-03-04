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
from lyra.core.cli_pool import CliPool
from lyra.core.message import Message, MessageContent, Response, TextContent
from lyra.core.pool import Pool

log = logging.getLogger(__name__)


def _extract_text(msg: Message) -> str:
    """Extract plain text from a Message, regardless of content type."""
    content: MessageContent | str = msg.content
    if isinstance(content, str):
        return content
    if isinstance(content, TextContent):
        return content.text
    # ImageContent / AudioContent — forward the URL as text for now
    return getattr(content, "url", str(content))


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

    def __init__(self, config: Agent, cli_pool: CliPool) -> None:
        super().__init__(config)
        self._pool = cli_pool

    async def process(self, msg: Message, pool: Pool) -> Response:
        text = _extract_text(msg)
        model_cfg = self.config.model_config

        log.debug(
            "[agent:%s][pool:%s] processing message (%d chars)",
            self.name,
            pool.pool_id,
            len(text),
        )

        result = await self._pool.send(pool.pool_id, text, model_cfg)

        if "error" in result:
            error_detail = result["error"]
            log.warning(
                "[agent:%s][pool:%s] CLI error: %s",
                self.name,
                pool.pool_id,
                error_detail,
            )
            # Timeout gets a specific message; all other errors get a generic one
            if "Timeout" in error_detail:
                user_msg = "Response timed out. Please try again."
            else:
                user_msg = "Something went wrong. Please try again."
            return Response(
                content=user_msg,
                metadata={"error": True},
            )

        reply = result.get("result", "")
        meta: dict[str, Any] = {"session_id": result.get("session_id", "")}
        if "warning" in result:
            meta["warning"] = result["warning"]

        return Response(content=reply, metadata=meta)
