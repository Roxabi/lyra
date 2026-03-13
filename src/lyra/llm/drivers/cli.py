"""ClaudeCliDriver — LlmProvider wrapping the existing CliPool."""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable

from lyra.core.agent import ModelConfig
from lyra.core.cli_pool import CliPool
from lyra.llm.base import LlmResult

log = logging.getLogger(__name__)


class ClaudeCliDriver:
    """LlmProvider adapter over CliPool.

    Translates CliPool.send() → LlmResult. CliPool manages session
    persistence and process lifecycle internally.
    """

    capabilities: dict = {"streaming": False, "auth": "oauth_only"}

    def __init__(self, pool: CliPool) -> None:
        self._pool = pool

    async def reset(self, pool_id: str) -> None:
        """Kill the CLI process for this pool. Next send() spawns a fresh one."""
        await self._pool.reset(pool_id)

    async def complete(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,  # ignored — CliPool manages history
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
    ) -> LlmResult:
        cli_result = await self._pool.send(
            pool_id, text, model_cfg, system_prompt, on_intermediate=on_intermediate
        )
        return LlmResult(
            result=cli_result.result,
            session_id=cli_result.session_id,
            error=cli_result.error,
            warning=cli_result.warning,
        )
