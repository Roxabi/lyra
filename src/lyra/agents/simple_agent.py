"""
SimpleAgent — first concrete AgentBase implementation.

Wraps an LlmProvider to route messages through the configured backend.
Model and backend are read from the agent's TOML config (ModelConfig),
not hardcoded here.
"""

from __future__ import annotations

import html
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lyra.core.agent import Agent, AgentBase
from lyra.core.agent_config import _AGENTS_DIR
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    Response,
)
from lyra.core.messages import MessageManager
from lyra.core.pool import Pool
from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder
from lyra.llm.base import LlmProvider
from lyra.stt import is_whisper_noise

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Awaitable, Callable

    from lyra.core.agent_store import AgentStore
    from lyra.stt import STTService
    from lyra.tts import TTSService

log = logging.getLogger(__name__)


class SimpleAgent(AgentBase):
    """Agent that routes every message through an LlmProvider.

    One LlmProvider instance is shared across all SimpleAgent instances —
    pass it in from the hub so it can be stopped cleanly on shutdown.

    Wiring (in main.py / hub bootstrap)::

        cli_pool = CliPool()
        await cli_pool.start()

        provider = ClaudeCliDriver(cli_pool)
        agent_config = load_agent_config("lyra_default")
        agent = SimpleAgent(agent_config, provider)
        hub.register_agent(agent)
    """

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        config: Agent,
        provider: LlmProvider,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        stt: "STTService | None" = None,
        tts: "TTSService | None" = None,
        runtime_config: RuntimeConfig | None = None,
        agents_dir: Path | None = None,
        agent_store: "AgentStore | None" = None,
    ) -> None:
        resolved_agents_dir = agents_dir or _AGENTS_DIR
        rc = (
            runtime_config
            if runtime_config is not None
            else RuntimeConfig.load(resolved_agents_dir / "lyra_runtime.toml")
        )
        self._runtime_config_holder = RuntimeConfigHolder(rc)
        self._runtime_config_path = resolved_agents_dir / "lyra_runtime.toml"
        self._provider = provider
        super().__init__(
            config,
            agents_dir=agents_dir,
            circuit_registry=circuit_registry,
            msg_manager=msg_manager,
            stt=stt,
            tts=tts,
            agent_store=agent_store,
        )

    def is_backend_alive(self, pool_id: str) -> bool:
        """Delegate to the LlmProvider's liveness check."""
        return self._provider.is_alive(pool_id)

    async def reset_backend(self, pool_id: str) -> None:
        """Kill the backend process so the next turn gets a fresh one."""
        reset_fn = getattr(self._provider, "reset", None)
        if reset_fn is not None:
            await reset_fn(pool_id)

    def _build_router_kwargs(self) -> dict[str, object]:
        return {
            "runtime_config_holder": self._runtime_config_holder,
            "runtime_config_path": self._runtime_config_path,
            "workspaces": self.config.workspaces,
        }

    def _rebuild_command_router(self) -> None:
        super()._rebuild_command_router()
        self._register_session_commands()

    def _register_session_commands(self) -> None:
        """Store SessionTools; register processor cmds as passthroughs (B2, #363)."""
        import lyra.core.processors  # noqa: F401 — trigger self-registration
        from lyra.core.processor_registry import registry
        from lyra.integrations.base import SessionTools
        from lyra.integrations.vault_cli import VaultCli
        from lyra.integrations.web_intel import WebIntelScraper

        try:
            self._session_tools = SessionTools(
                scraper=WebIntelScraper(), vault=VaultCli()
            )
        except Exception:
            log.warning(
                "SimpleAgent: could not build session tools"
                " — processor pipeline disabled",
                exc_info=True,
            )
            self._session_tools = None
            return

        for cmd in registry.commands():
            self.command_router.register_passthrough(cmd.lstrip("/"))

    def _maybe_register_reset(self, pool: Pool) -> None:
        """Register a session reset callback on the pool the first time we process.

        /clear calls pool.reset_session(), which delegates here → CliPool.reset().
        """
        if pool._session_reset_fn is None:
            reset_fn = getattr(self._provider, "reset", None)
            if reset_fn is not None:
                _pool_id = pool.pool_id
                pool._session_reset_fn = lambda: reset_fn(_pool_id)

        switch_fn = getattr(self._provider, "switch_cwd", None)
        if switch_fn is not None and pool._switch_workspace_fn is None:
            _pool_id = pool.pool_id
            pool._switch_workspace_fn = lambda cwd: switch_fn(_pool_id, cwd)

    def _maybe_register_resume(self, pool: Pool) -> None:
        """Register session resume callback on the pool the first time we process.

        Hub calls pool.resume_session(session_id) → delegates here →
        CliPool.resume_and_reset(). Follows the same lazy-wiring pattern as
        _maybe_register_reset.
        """
        if pool._session_resume_fn is None:
            resume_fn = getattr(self._provider, "resume_and_reset", None)
            if resume_fn is not None:
                _pool_id = pool.pool_id
                pool._session_resume_fn = lambda sid: resume_fn(_pool_id, sid)

    def configure_pool(self, pool: Pool) -> None:
        """Wire provider callbacks onto *pool* before first message is processed.

        Moved out of process() so that pool._session_resume_fn is set before
        _resolve_context() calls pool.resume_session() on the first message
        after a daemon restart.
        """
        self._maybe_register_reset(pool)
        self._maybe_register_resume(pool)

    async def process(  # noqa: C901
        self,
        msg: InboundMessage,
        pool: Pool,
        *,
        on_intermediate: "Callable[[str], Awaitable[None]] | None" = None,
    ) -> "Response | AsyncIterator[str]":
        self._maybe_reload()

        # /voice pre-router: rewrite as voice-modality LLM request
        _voice_rewritten = self._handle_voice_command(msg)
        if _voice_rewritten is not None:
            msg = _voice_rewritten

        # Handle audio messages — attachments with type="audio"
        audio_attachment = next((a for a in msg.attachments if a.type == "audio"), None)
        if audio_attachment is not None:
            tmp_path = Path(str(audio_attachment.url_or_path_or_bytes))
            try:
                if self._stt is None:
                    return Response(
                        content=(
                            self._msg_manager.get("stt_unsupported")
                            if self._msg_manager
                            else (
                                "Voice messages are not supported"
                                " — STT is not configured."
                            )
                        )
                    )
                stt_result = await self._stt.transcribe(tmp_path)
            except Exception:
                log.exception("STT transcription failed in SimpleAgent")
                return Response(
                    content=(
                        self._msg_manager.get("stt_failed")
                        if self._msg_manager
                        else "Sorry, I couldn't transcribe your voice message."
                    ),
                    metadata={"error": True},
                )
            finally:
                tmp_path.unlink(missing_ok=True)
            if is_whisper_noise(stt_result.text):
                return Response(
                    content=(
                        self._msg_manager.get("stt_noise")
                        if self._msg_manager
                        else "I couldn't make out your voice message, please try again."
                    )
                )
            _esc = html.escape(stt_result.text)
            text = f"<voice_transcript>{_esc}</voice_transcript>"
        elif msg.modality == "voice":
            # Pipeline-transcribed audio — wrap for prompt injection guard (H-8)
            text = f"<voice_transcript>{html.escape(msg.text)}</voice_transcript>"
        else:
            text = msg.text

        model_cfg = self.config.model_config

        log.debug(
            "[agent:%s][pool:%s] processing message (%d chars)",
            self.name,
            pool.pool_id,
            len(text),
        )

        # Streaming path: return AsyncIterator directly for pool_processor routing
        _stream_fn = getattr(self._provider, "stream", None)
        if model_cfg.streaming and _stream_fn is not None:
            cb = on_intermediate if self.config.show_intermediate else None
            return await _stream_fn(
                pool.pool_id,
                text,
                model_cfg,
                pool._system_prompt or self.config.system_prompt,
                on_intermediate=cb,
            )

        # Use injected callback only if show_intermediate is enabled
        cb = on_intermediate if self.config.show_intermediate else None

        result = await self._provider.complete(
            pool.pool_id,
            text,
            model_cfg,
            pool._system_prompt or self.config.system_prompt,
            on_intermediate=cb,
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
                user_msg = (
                    self._msg_manager.get("timeout")
                    if self._msg_manager
                    else "Your request timed out. Please try again."
                )
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

        return Response(content=reply, metadata=meta, speak=(msg.modality == "voice"))
