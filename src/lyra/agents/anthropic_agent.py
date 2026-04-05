"""AnthropicAgent — direct Anthropic SDK agent.

Calls the Messages API via an LlmProvider (AnthropicSdkDriver), handling STT,
conversation history, and system prompt injection. Opt-in via
backend = "anthropic-sdk" in agent TOML config.
"""

from __future__ import annotations

import html
import importlib
import logging
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.core.agent import Agent, AgentBase
from lyra.core.agent_config import ModelConfig
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import InboundMessage, Response
from lyra.core.messages import MessageManager
from lyra.core.pool import Pool
from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder
from lyra.llm.base import LlmProvider
from lyra.stt import is_whisper_noise

_AGENTS_DIR = Path(__file__).resolve().parent

if TYPE_CHECKING:
    from lyra.core.stores.agent_store import AgentStore
    from lyra.stt import STTProtocol
    from lyra.tts import TtsProtocol

log = logging.getLogger(__name__)


class AnthropicAgent(AgentBase):
    """Agent that calls the Anthropic Messages API via an LlmProvider.

    Delegates all SDK interaction to the injected provider. Returns a
    complete Response rather than streaming — adapters receive the full reply.
    """

    def __init__(  # noqa: PLR0913 — DI constructor, each arg is a required dependency
        self,
        config: Agent,
        provider: LlmProvider,
        circuit_registry: CircuitRegistry | None = None,
        msg_manager: MessageManager | None = None,
        runtime_config: RuntimeConfig | None = None,
        stt: "STTProtocol | None" = None,
        tts: "TtsProtocol | None" = None,
        agents_dir: Path | None = None,
        smart_routing_decorator: object | None = None,
        agent_store: "AgentStore | None" = None,
    ) -> None:
        resolved_agents_dir: Path = agents_dir or _AGENTS_DIR
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
            smart_routing_decorator=smart_routing_decorator,
            agent_store=agent_store,
        )

    @property
    def runtime_config(self) -> RuntimeConfig:
        """Current runtime config. Always reflects the latest /config set."""
        return self._runtime_config_holder.value

    def _build_router_kwargs(self) -> dict[str, object]:
        return {
            "runtime_config_holder": self._runtime_config_holder,
            "runtime_config_path": self._runtime_config_path,
            "session_driver": self._provider,
        }

    def _rebuild_command_router(self) -> None:
        super()._rebuild_command_router()
        self._register_session_commands()

    def _register_session_commands(self) -> None:
        """Store SessionTools and register processor commands as passthroughs.

        Replaces the old session-command registrations (cmd_add, cmd_explain,
        cmd_summarize, cmd_search) — B2, issue #363.  The processor pipeline
        in pool_processor.py now handles these commands via pre()/post() hooks
        on the normal pool flow, so responses land in pool history and enable
        follow-up questions.
        """
        importlib.import_module("lyra.core.processors")  # trigger self-registration
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
                "AnthropicAgent: could not build session tools"
                " — processor pipeline disabled",
                exc_info=True,
            )
            self._session_tools = None
            return

        # Register each processor command as a passthrough so command_router.dispatch()
        # returns None (agent-handled) rather than "unknown command".
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

    async def _process_llm(  # noqa: C901 — voice modality branch adds one branch
        self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
    ) -> Response:
        """Call the LlmProvider, handle STT, update history, return Response."""
        self._maybe_reload()
        effective = self.runtime_config.overlay(self.config)

        tmp_path: Path | None = None
        llm_text: str
        history_text: str

        # Detect audio attachment and resolve local path once for all AUDIO paths
        _audio = next((a for a in msg.attachments if a.type == "audio"), None)
        if _audio is not None:
            tmp_path = Path(str(_audio.url_or_path_or_bytes))

        # outer try: temp-file cleanup (ADR-013)
        try:
            if tmp_path is not None and self._stt is not None:
                stt_result = await self._stt.transcribe(tmp_path)
                if is_whisper_noise(stt_result.text):
                    tmp_path.unlink(missing_ok=True)
                    tmp_path = None  # prevent double-unlink in outer finally
                    _noise_msg = (
                        self._msg_manager.get("stt_noise")
                        if self._msg_manager
                        else (
                            "I couldn't make out your voice message, please try again."
                        )
                    )
                    return Response(content=_noise_msg)
                _esc = html.escape(stt_result.text)
                llm_text = f"<voice_transcript>{_esc}</voice_transcript>"
                history_text = stt_result.text
            elif msg.modality == "voice":
                # Pipeline-transcribed audio — wrap for prompt injection guard (H-8)
                _esc = html.escape(msg.text)
                llm_text = f"<voice_transcript>{_esc}</voice_transcript>"
                history_text = msg.text
            else:
                if not msg.processor_enriched:
                    llm_text = f"<user_message>{html.escape(msg.text)}</user_message>"
                else:
                    llm_text = msg.text
                history_text = msg.text

            # Build messages array for SDK (includes history + new user message)
            messages: list[dict] = list(pool.sdk_history)
            messages.append({"role": "user", "content": history_text})

            # Build effective ModelConfig from overlay
            effective_cfg = ModelConfig(
                backend="anthropic-sdk",
                model=effective.model,
                max_turns=effective.max_turns,
                tools=self.config.llm_config.tools,
            )

            try:
                result = await self._provider.complete(
                    pool.pool_id,
                    llm_text,
                    effective_cfg,
                    effective.system_prompt or "",
                    messages=messages,
                )
            except Exception:
                log.exception("AnthropicAgent: provider.complete() failed")
                raise  # let pool record CB failure

            if not result.ok:
                log.warning("[agent:%s] provider error: %s", self.name, result.error)
                user_msg = (
                    self._msg_manager.get("generic")
                    if self._msg_manager
                    else "Sorry, something went wrong. Please try again."
                )
                return Response(content=user_msg, metadata={"error": True})

            # Persist to SDK history: user turn + assistant reply
            pool.extend_sdk_history(
                [
                    {"role": "user", "content": history_text},
                    {"role": "assistant", "content": result.result},
                ]
            )

            log.info(
                "[agent:%s][pool:%s] response: %d chars",
                self.name,
                pool.pool_id,
                len(result.result),
            )
            return Response(content=result.result)
        finally:
            if tmp_path is not None:
                tmp_path.unlink(missing_ok=True)

    async def process(
        self, msg: InboundMessage, pool: Pool, *, on_intermediate=None
    ) -> Response:
        """Rewrite /voice commands as voice-modality LLM requests, then process."""
        _voice_rewritten = self._handle_voice_command(msg)
        if _voice_rewritten is not None:
            msg = _voice_rewritten
        response = await self._process_llm(msg, pool, on_intermediate=on_intermediate)
        if msg.modality == "voice":
            response.speak = True
        return response
