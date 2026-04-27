"""
SimpleAgent — first concrete AgentBase implementation.

Wraps an LlmProvider to route messages through the configured backend.
Model and backend are read from the agent's TOML config (ModelConfig),
not hardcoded here.
"""

from __future__ import annotations

import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lyra.core.agent import Agent, AgentBase
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.messaging.message import (
    GENERIC_ERROR_REPLY,
    InboundMessage,
    Response,
)
from lyra.core.messaging.messages import MessageManager
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.core.pool import Pool
from lyra.core.processors.stream_processor import StreamProcessor
from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder
from lyra.integrations.base import SessionTools
from lyra.llm.base import LlmProvider

from .simple_agent_prompts import STTError, STTNoiseError, build_llm_text

_AGENTS_DIR = Path(__file__).resolve().parent

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from lyra.core.cli.cli_pool import CliPool
    from lyra.core.messaging.render_events import RenderEvent
    from lyra.infrastructure.stores.agent_store import AgentStore
    from lyra.stt import STTProtocol
    from lyra.tts import TtsProtocol

log = logging.getLogger(__name__)


class SimpleAgent(AgentBase):
    """Agent that routes every message through an LlmProvider.

    One LlmProvider instance is shared across all SimpleAgent instances —
    pass it in from the hub so it can be stopped cleanly on shutdown.

    Wiring (in main.py / hub bootstrap)::

        cli_pool = CliPool()
        await cli_pool.start()

        provider = ClaudeCliDriver(cli_pool)
        agent_config = agent_row_to_config(store.get("lyra_default"))
        agent = SimpleAgent(agent_config, provider)
        hub.register_agent(agent)
    """

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        config: Agent,
        provider: LlmProvider,
        cli_pool: CliPool | None = None,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        stt: "STTProtocol | None" = None,
        tts: "TtsProtocol | None" = None,
        runtime_config: RuntimeConfig | None = None,
        agents_dir: Path | None = None,
        agent_store: "AgentStore | None" = None,
        tool_display_config: ToolDisplayConfig | None = None,
        session_tools: SessionTools | None = None,
    ) -> None:
        self._tool_display_config = tool_display_config or ToolDisplayConfig()
        resolved_agents_dir = agents_dir or _AGENTS_DIR
        rc = (
            runtime_config
            if runtime_config is not None
            else RuntimeConfig.load(resolved_agents_dir / "lyra_runtime.toml")
        )
        self._runtime_config_holder = RuntimeConfigHolder(rc)
        self._runtime_config_path = resolved_agents_dir / "lyra_runtime.toml"
        self._provider = provider
        self._cli_pool = cli_pool
        self._session_tools = session_tools
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
        if self._cli_pool is not None:
            await self._cli_pool.reset(pool_id)

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
        """Register processor cmds as passthroughs; uses injected SessionTools."""
        importlib.import_module("lyra.core.processors")  # trigger self-registration
        from lyra.core.processors.processor_registry import registry

        if self._session_tools is None:
            # Transitional fallback: construct locally until all callers inject.
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

        # /add-vault — direct vault save, no LLM needed (#372).
        from lyra.commands.add_vault.handlers import cmd_add_vault

        self.command_router.register_session_command(
            "add-vault",
            cmd_add_vault,
            tools=self._session_tools,
            description="Save a note to the vault: /add-vault <note content>",
        )

    def _maybe_register_reset(self, pool: Pool) -> None:
        """Register session reset/switch callbacks on the pool."""
        _cli_pool = self._cli_pool  # narrow once; stable capture for lambdas
        if _cli_pool is not None:
            _pool_id = pool.pool_id
            pool.register_session_callbacks(
                reset_fn=lambda: _cli_pool.reset(_pool_id),
                workspace_fn=lambda cwd: _cli_pool.switch_cwd(_pool_id, cwd),
            )

    def _maybe_register_resume(self, pool: Pool) -> None:
        """Register session resume callback on the pool.

        Hub calls pool.resume_session(session_id) → delegates here →
        CliPool.resume_and_reset(). Follows the same lazy-wiring pattern as
        _maybe_register_reset.
        """
        _cli_pool = self._cli_pool  # narrow once; stable capture for lambda
        if _cli_pool is not None:
            _pool_id = pool.pool_id
            pool.register_session_callbacks(
                resume_fn=lambda sid: _cli_pool.resume_and_reset(_pool_id, sid),
            )

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
    ) -> "Response | AsyncIterator[RenderEvent]":
        self._maybe_reload()

        # /voice pre-router: rewrite as voice-modality LLM request
        _voice_rewritten = self._handle_voice_command(msg)
        if _voice_rewritten is not None:
            msg = _voice_rewritten

        # Build LLM text from message (handles audio, voice, regular messages)
        try:
            text, _stt_text = await build_llm_text(msg, self._stt)
        except STTNoiseError:
            return Response(
                content=(
                    self._msg_manager.get("stt_noise")
                    if self._msg_manager
                    else "I couldn't make out your voice message, please try again."
                )
            )
        except STTError:
            log.exception("STT transcription failed in SimpleAgent")
            return Response(
                content=(
                    self._msg_manager.get("stt_failed")
                    if self._msg_manager
                    else "Sorry, I couldn't transcribe your voice message."
                ),
                metadata={"error": True},
            )

        model_cfg = self.config.llm_config

        # Link Lyra session → CLI session so reply-to-resume works.
        if self._cli_pool is not None:
            self._cli_pool.link_lyra_session(pool.pool_id, pool.session_id)

        log.debug(
            "[agent:%s][pool:%s] processing message (%d chars)",
            self.name,
            pool.pool_id,
            len(text),
        )

        # Streaming path: wrap with StreamProcessor to emit RenderEvent (#387)
        _stream_fn = getattr(self._provider, "stream", None)
        if model_cfg.streaming and _stream_fn is not None:
            stream_iter = await _stream_fn(
                pool.pool_id,
                text,
                model_cfg,
                pool._system_prompt or self.config.system_prompt,
            )
            processor = StreamProcessor(
                config=self._tool_display_config,
                show_intermediate=self.config.show_intermediate,
            )
            return processor.process(stream_iter)

        result = await self._provider.complete(
            pool.pool_id,
            text,
            model_cfg,
            pool._system_prompt or self.config.system_prompt,
        )

        if not result.ok:
            log.warning(
                "[agent:%s][pool:%s] CLI error: %s",
                self.name,
                pool.pool_id,
                result.error,
            )
            pool._last_turn_had_backend_error = True
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
