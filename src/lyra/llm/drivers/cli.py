"""ClaudeCliDriver — LlmProvider wrapping the existing CliPool."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path

from lyra.core.agent_config import ModelConfig
from lyra.core.cli_pool import CliPool
from lyra.llm.base import LlmResult
from lyra.llm.events import LlmEvent

log = logging.getLogger(__name__)


class ClaudeCliDriver:
    """LlmProvider adapter over CliPool.

    Translates CliPool.send() → LlmResult. CliPool manages session
    persistence and process lifecycle internally.
    """

    capabilities: dict = {"streaming": True, "auth": "oauth_only"}

    def __init__(self, pool: CliPool) -> None:
        self._pool = pool

    async def reset(self, pool_id: str) -> None:
        """Kill the CLI process for this pool. Next send() spawns a fresh one."""
        await self._pool.reset(pool_id)

    async def switch_cwd(self, pool_id: str, cwd: Path) -> None:
        """Delegate workspace switch to CliPool."""
        await self._pool.switch_cwd(pool_id, cwd)

    async def resume_and_reset(self, pool_id: str, session_id: str) -> bool:
        """Delegate session resume to CliPool.

        Wired into pool._session_resume_fn by SimpleAgent._maybe_register_resume
        so that pool.resume_session(sid) → CliPool.resume_and_reset(pool_id, sid).
        Returns True if the resume was accepted, False if skipped.
        """
        return await self._pool.resume_and_reset(pool_id, session_id)

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        """Register the current Lyra session for CLI session mapping."""
        self._pool.link_lyra_session(pool_id, lyra_session_id)

    def is_alive(self, pool_id: str) -> bool:
        """Delegate liveness check to CliPool."""
        return self._pool.is_alive(pool_id)

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

    async def stream(
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,  # protocol compliance; ignored by CliPool
    ) -> AsyncIterator[LlmEvent]:
        """Return a streaming iterator yielding LlmEvent objects.

        Yields TextLlmEvent for text chunks, ToolUseLlmEvent when the LLM
        calls a tool, and a terminal ResultLlmEvent at end of turn.
        """
        return await self._pool.send_streaming(
            pool_id, text, model_cfg, system_prompt, on_intermediate=None
        )
