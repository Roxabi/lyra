"""AnthropicAgent — direct Anthropic SDK agent.

Calls the Messages API via AsyncAnthropic with streaming, tool use,
and system prompt injection. Opt-in via backend = "anthropic-sdk" in
agent TOML config.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import Any

import anthropic

from lyra.agents.simple_agent import _extract_text
from lyra.core.agent import Agent, AgentBase
from lyra.core.message import Message
from lyra.core.pool import Pool

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_time",
        "description": "Get the current date and time in UTC",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


class AnthropicAgent(AgentBase):
    """Agent that calls the Anthropic Messages API directly.

    Supports streaming (async generator), tool use, and system prompt
    injection from agent TOML config.
    """

    def __init__(self, config: Agent) -> None:
        super().__init__(config)
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("Missing required env var: ANTHROPIC_API_KEY")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    def _build_messages(self, text: str, pool: Pool) -> list[dict[str, Any]]:
        """Build SDK messages array from pool history + new user message."""
        messages: list[dict[str, Any]] = list(pool.sdk_history)
        messages.append({"role": "user", "content": text})
        return messages

    def _execute_tool(self, name: str, tool_input: dict) -> str:
        """Execute a tool by name and return result as string."""
        if name == "get_time":
            return datetime.now(timezone.utc).isoformat()
        raise ValueError(f"Unknown tool: {name}")

    async def process(  # type: ignore[override]
        self, msg: Message, pool: Pool
    ) -> AsyncIterator[str]:
        """Stream response from Anthropic API, handling tool use loops.

        Yields text deltas for the adapter to display progressively.
        """
        self._maybe_reload()
        text = _extract_text(msg)
        messages = self._build_messages(text, pool)

        kwargs: dict[str, Any] = {
            "model": self.config.model_config.model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if self.config.system_prompt:
            kwargs["system"] = self.config.system_prompt
        if self.config.model_config.tools:
            kwargs["tools"] = TOOLS

        accumulated_text = ""
        max_turns = self.config.model_config.max_turns
        final: Any = None

        for _turn in range(max_turns):
            async with self._client.messages.stream(**kwargs) as stream:
                async for delta in stream.text_stream:
                    accumulated_text += delta
                    yield delta
                final = stream.get_final_message()

            log.info(
                "SDK stream: in=%d out=%d tokens",
                final.usage.input_tokens,
                final.usage.output_tokens,
            )

            if final.stop_reason != "tool_use":
                break

            # Handle tool use blocks
            tool_results: list[dict[str, Any]] = []
            for block in final.content:
                if block.type == "tool_use":
                    try:
                        result = self._execute_tool(block.name, block.input)
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": result,
                            }
                        )
                    except Exception as e:
                        tool_results.append(
                            {
                                "type": "tool_result",
                                "tool_use_id": block.id,
                                "content": str(e),
                                "is_error": True,
                            }
                        )

            # Continue conversation with tool results
            kwargs["messages"] = [
                *kwargs["messages"],
                {"role": "assistant", "content": final.content},
                {"role": "user", "content": tool_results},
            ]

        # Append exchange to pool history
        reply_text = accumulated_text
        if not reply_text and final and final.content:
            for block in final.content:
                if hasattr(block, "text"):
                    reply_text = block.text
                    break

        pool.append_sdk_exchange(
            {"role": "user", "content": text},
            {"role": "assistant", "content": reply_text},
        )
