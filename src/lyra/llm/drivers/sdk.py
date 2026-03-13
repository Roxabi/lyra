"""AnthropicSdkDriver — LlmProvider implementation wrapping AsyncAnthropic."""

from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

import anthropic

from lyra.core.agent import ModelConfig
from lyra.llm.base import LlmResult

log = logging.getLogger(__name__)

TOOLS: list[dict[str, Any]] = [
    {
        "name": "get_time",
        "description": "Get the current date and time in UTC",
        "input_schema": {"type": "object", "properties": {}, "required": []},
    },
]


class AnthropicSdkDriver:
    """LlmProvider that calls the Anthropic Messages API directly.

    Buffers full streaming response internally. Handles tool-use loop.
    capabilities["streaming"] is False — callers receive complete text.
    """

    capabilities: dict = {"streaming": False, "auth": "api_key"}

    def __init__(self, api_key: str) -> None:
        self._client = anthropic.AsyncAnthropic(api_key=api_key)

    async def complete(  # noqa: C901, PLR0913 — tool-use loop with per-error-type handling; extracting would obscure the protocol flow
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
        # accepted for protocol compliance; not dispatched — SDK buffers full response
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> LlmResult:
        """Buffer full response including tool-use loop. Return LlmResult."""
        if on_intermediate is not None:
            log.debug("[sdk] on_intermediate not supported; callback ignored")
        if messages is None:
            messages = [{"role": "user", "content": text}]

        kwargs: dict[str, Any] = {
            "model": model_cfg.model,
            "max_tokens": 4096,
            "messages": messages,
        }
        if system_prompt:
            kwargs["system"] = system_prompt
        if model_cfg.tools:
            kwargs["tools"] = [t for t in TOOLS if t["name"] in model_cfg.tools]

        try:
            accumulated_text = ""
            max_turns = model_cfg.max_turns
            final: Any = None

            for _turn in range(max_turns):
                turn_text = ""
                async with self._client.messages.stream(**kwargs) as stream:
                    async for delta in stream.text_stream:
                        turn_text += delta
                    final = await stream.get_final_message()
                accumulated_text = turn_text

                log.info(
                    "SDK stream [pool:%s]: in=%d out=%d tokens",
                    pool_id,
                    final.usage.input_tokens,
                    final.usage.output_tokens,
                )

                if final.stop_reason != "tool_use":
                    break

                # Handle tool use
                tool_results: list[dict[str, Any]] = []
                for block in final.content:
                    if block.type == "tool_use":
                        try:
                            result_str = await self._execute_tool(
                                block.name, block.input
                            )
                            tool_results.append(
                                {
                                    "type": "tool_result",
                                    "tool_use_id": block.id,
                                    "content": result_str,
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

                kwargs["messages"] = [
                    *kwargs["messages"],
                    {"role": "assistant", "content": final.content},
                    {"role": "user", "content": tool_results},
                ]
            else:
                # max_turns exhausted
                if final is not None and final.stop_reason == "tool_use":
                    accumulated_text += " [max tool turns reached]"

            # Fallback if accumulated_text empty but final has text
            if not accumulated_text and final is not None and final.content:
                for block in final.content:
                    if hasattr(block, "text"):
                        accumulated_text = block.text
                        break

            return LlmResult(result=accumulated_text)

        except anthropic.AuthenticationError:
            log.debug("AnthropicSdkDriver auth error [pool:%s]", pool_id, exc_info=True)
            return LlmResult(error="provider_auth_error")
        except anthropic.RateLimitError:
            log.debug("AnthropicSdkDriver rate limit [pool:%s]", pool_id, exc_info=True)
            return LlmResult(error="provider_rate_limit")
        except anthropic.APIError:
            log.debug("AnthropicSdkDriver API error [pool:%s]", pool_id, exc_info=True)
            return LlmResult(error="provider_api_error")
        except Exception:
            log.debug(
                "AnthropicSdkDriver unexpected error [pool:%s]", pool_id, exc_info=True
            )
            return LlmResult(error="provider_error")

    def is_alive(self, pool_id: str) -> bool:
        return True  # SDK backend is always reachable (no persistent process)

    async def _execute_tool(self, name: str, tool_input: dict) -> str:
        if name == "get_time":
            return datetime.now(timezone.utc).isoformat()
        raise ValueError(f"Unknown tool: {name}")
