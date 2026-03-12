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

from lyra.core.agent import _AGENTS_DIR, Agent, AgentBase
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import Message, extract_text
from lyra.core.messages import MessageManager
from lyra.core.pool import Pool
from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder

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

    def __init__(
        self,
        config: Agent,
        circuit_registry: CircuitRegistry | None = None,
        admin_user_ids: set[str] | None = None,
        msg_manager: MessageManager | None = None,
        runtime_config: RuntimeConfig | None = None,
    ) -> None:
        rc = runtime_config if runtime_config is not None else RuntimeConfig.load(
            _AGENTS_DIR / "lyra_runtime.toml"
        )
        self._runtime_config_holder = RuntimeConfigHolder(rc)
        super().__init__(
            config,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
        )
        api_key = os.environ.get("ANTHROPIC_API_KEY")
        if not api_key:
            raise SystemExit("Missing required env var: ANTHROPIC_API_KEY")
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    @property
    def runtime_config(self) -> RuntimeConfig:
        """Current runtime config. Always reflects the latest /config set."""
        return self._runtime_config_holder.value

    @property
    def _runtime_config(self) -> RuntimeConfig:
        return self.runtime_config

    def _build_router_kwargs(self) -> dict[str, object]:
        return {"runtime_config_holder": self._runtime_config_holder}

    def _build_messages(self, text: str, pool: Pool) -> list[dict[str, Any]]:
        """Build SDK messages array from pool history + new user message."""
        messages: list[dict[str, Any]] = list(pool.sdk_history)
        messages.append({"role": "user", "content": text})
        return messages

    async def _execute_tool(self, name: str, tool_input: dict) -> str:
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
        effective = self._runtime_config.overlay(self.config)
        text = extract_text(msg)
        messages = self._build_messages(text, pool)

        kwargs: dict[str, Any] = {
            "model": effective.model,
            "max_tokens": 4096,
            "temperature": effective.temperature,
            "messages": messages,
        }
        if effective.system_prompt:
            kwargs["system"] = effective.system_prompt
        if self.config.model_config.tools:
            kwargs["tools"] = [
                t for t in TOOLS if t["name"] in self.config.model_config.tools
            ]

        accumulated_text = ""
        max_turns = effective.max_turns
        final: Any = None
        new_messages: list[dict[str, Any]] = [{"role": "user", "content": text}]

        try:
            for _turn in range(max_turns):
                async with self._client.messages.stream(**kwargs) as stream:
                    async for delta in stream.text_stream:
                        accumulated_text += delta
                        yield delta
                    final = await stream.get_final_message()

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
                            result = await self._execute_tool(block.name, block.input)
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result,
                                }
                            )
                        except Exception:
                            log.exception("Tool %s failed", block.name)
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": "Tool execution failed.",
                                    "is_error": True,
                                }
                            )

                # Track intermediate turns for history
                assistant_turn = {"role": "assistant", "content": final.content}
                tool_turn = {"role": "user", "content": tool_results}
                new_messages.append(assistant_turn)
                new_messages.append(tool_turn)

                # Continue conversation with tool results
                kwargs["messages"] = [
                    *kwargs["messages"],
                    assistant_turn,
                    tool_turn,
                ]
            else:
                # max_turns exhausted
                if final and final.stop_reason == "tool_use":
                    yield " [max tool turns reached]"
            # Build final assistant message for history
            reply_text = accumulated_text
            if not reply_text and final and final.content:
                for block in final.content:
                    if hasattr(block, "text"):
                        reply_text = block.text
                        break
            new_messages.append({"role": "assistant", "content": reply_text})
        except Exception:
            log.exception("Streaming error in AnthropicAgent")
            raise  # re-raise so Hub.dispatch_streaming() can record circuit failure
        finally:
            # Persist history even on failure so partial turns survive
            if len(new_messages) > 1:
                pool.extend_sdk_history(new_messages)
