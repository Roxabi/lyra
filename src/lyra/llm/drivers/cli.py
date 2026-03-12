"""ClaudeCliDriver — LlmProvider wrapping the existing CliPool."""

from __future__ import annotations

import logging

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

    async def complete(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,  # ignored — CliPool manages history
    ) -> LlmResult:
        cli_result = await self._pool.send(pool_id, text, model_cfg, system_prompt)
        return LlmResult(
            result=cli_result.result,
            session_id=cli_result.session_id,
            error=cli_result.error,
            warning=cli_result.warning,
        )
