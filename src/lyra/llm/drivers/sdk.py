"""AnthropicSdkDriver — LlmProvider implementation wrapping AsyncAnthropic."""

from __future__ import annotations

import logging
import time
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

import anthropic

from lyra.core.agent.agent_config import ModelConfig
from lyra.core.messaging.events import (
    LlmEvent,
    ResultLlmEvent,
    TextLlmEvent,
    ToolUseLlmEvent,
)
from lyra.errors import (
    ProviderApiError,
    ProviderAuthError,
    ProviderError,
    ProviderRateLimitError,
)
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

    async def complete(  # noqa: C901, PLR0913, PLR0915 — tool-use loop with per-error-type handling; extracting would obscure the protocol flow
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
            # None = unlimited; fall back to a large safety cap so we never
            # spin forever in case the model keeps returning tool_use.
            _MAX_TURNS_SAFETY = 1000
            max_turns = model_cfg.max_turns or _MAX_TURNS_SAFETY
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

        except anthropic.AuthenticationError as exc:
            _code = getattr(exc, "status_code", 401)
            log.error(
                "AnthropicSdkDriver auth error [pool:%s] status=%s",
                pool_id,
                _code,
            )
            raise ProviderAuthError(
                str(exc), status_code=_code, provider="anthropic"
            ) from exc
        except anthropic.RateLimitError as exc:
            _code = getattr(exc, "status_code", 429)
            log.warning(
                "AnthropicSdkDriver rate limit [pool:%s] status=%s",
                pool_id,
                _code,
            )
            raise ProviderRateLimitError(
                str(exc), status_code=_code, provider="anthropic"
            ) from exc
        except anthropic.APIError as exc:
            log.debug("AnthropicSdkDriver API error [pool:%s]", pool_id, exc_info=True)
            _code = getattr(exc, "status_code", None)
            raise ProviderApiError(
                str(exc), status_code=_code, provider="anthropic"
            ) from exc
        except ProviderError:
            raise  # already wrapped
        except Exception as exc:
            log.debug(
                "AnthropicSdkDriver unexpected error [pool:%s]", pool_id, exc_info=True
            )
            raise ProviderError(str(exc), provider="anthropic", retryable=True) from exc

    async def stream(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Return an async iterator of LlmEvents for a single-turn streaming request.

        Emits TextLlmEvent per text delta, ToolUseLlmEvent at content block
        start for tool_use blocks, and a final ResultLlmEvent with timing.
        Single-turn only — no tool-use loop.
        """
        return self._stream_gen(
            pool_id, text, model_cfg, system_prompt, messages=messages
        )

    async def _stream_gen(  # noqa: C901, PLR0913, PLR0912
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
    ) -> AsyncIterator[LlmEvent]:
        """Async generator: yield LlmEvents for a single streaming turn."""
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

        t0 = time.monotonic()
        cost_usd: float | None = None
        try:
            async with self._client.messages.stream(**kwargs) as stream:
                async for event in stream:
                    event_type = getattr(event, "type", "")
                    if event_type == "content_block_start":
                        cb = getattr(event, "content_block", None)
                        if cb is not None and getattr(cb, "type", "") == "tool_use":
                            yield ToolUseLlmEvent(
                                tool_name=cb.name,
                                tool_id=cb.id,
                                input={},
                            )
                    elif event_type == "content_block_delta":
                        delta = getattr(event, "delta", None)
                        if delta is not None:
                            if getattr(delta, "type", "") == "text_delta":
                                yield TextLlmEvent(text=delta.text)

                try:
                    final = await stream.get_final_message()
                    cost_usd = getattr(final.usage, "cost_usd", None)
                except Exception:
                    cost_usd = None

            duration_ms = int((time.monotonic() - t0) * 1000)
            yield ResultLlmEvent(
                is_error=False, duration_ms=duration_ms, cost_usd=cost_usd
            )
        except Exception:
            yield ResultLlmEvent(
                is_error=True,
                duration_ms=int((time.monotonic() - t0) * 1000),
                cost_usd=None,
            )
            return  # terminate cleanly — is_error=True is the sentinel

    def is_alive(self, pool_id: str) -> bool:
        return True  # SDK backend is always reachable (no persistent process)

    async def _execute_tool(self, name: str, tool_input: dict) -> str:
        if name == "get_time":
            return datetime.now(timezone.utc).isoformat()
        raise ValueError(f"Unknown tool: {name}")
