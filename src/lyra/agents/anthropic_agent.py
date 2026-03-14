"""AnthropicAgent — direct Anthropic SDK agent.

Calls the Messages API via an LlmProvider (AnthropicSdkDriver), handling STT,
conversation history, and system prompt injection. Opt-in via
backend = "anthropic-sdk" in agent TOML config.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING

from lyra.core.agent import _AGENTS_DIR, Agent, AgentBase, ModelConfig
from lyra.core.circuit_breaker import CircuitRegistry
from lyra.core.message import InboundMessage, Response
from lyra.core.messages import MessageManager
from lyra.core.pool import Pool
from lyra.core.runtime_config import RuntimeConfig, RuntimeConfigHolder
from lyra.llm.base import LlmProvider
from lyra.stt import is_whisper_noise

if TYPE_CHECKING:
    from lyra.stt import STTService
    from lyra.tts import TTSService

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
        admin_user_ids: set[str] | None = None,
        msg_manager: MessageManager | None = None,
        runtime_config: RuntimeConfig | None = None,
        stt: "STTService | None" = None,
        tts: "TTSService | None" = None,
        agents_dir: Path | None = None,
        smart_routing_decorator: object | None = None,
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
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt,
            tts=tts,
            smart_routing_decorator=smart_routing_decorator,
        )

    @property
    def runtime_config(self) -> RuntimeConfig:
        """Current runtime config. Always reflects the latest /config set."""
        return self._runtime_config_holder.value

    def _build_router_kwargs(self) -> dict[str, object]:
        return {
            "runtime_config_holder": self._runtime_config_holder,
            "runtime_config_path": self._runtime_config_path,
        }

    async def _process_llm(
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
            if _audio is not None and self._stt is not None:
                stt_result = await self._stt.transcribe(tmp_path)  # type: ignore[arg-type]
                if is_whisper_noise(stt_result.text):
                    if tmp_path is not None:
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
                llm_text = f"\U0001f3a4 [transcribed]: {stt_result.text}"
                history_text = stt_result.text
            elif _audio is not None and self._stt is None:
                if tmp_path is not None:
                    tmp_path.unlink(missing_ok=True)
                    tmp_path = None  # prevent double-unlink in outer finally
                return Response(
                    content=(
                        self._msg_manager.get("stt_unsupported")
                        if self._msg_manager
                        else "Voice messages are not supported — STT is not configured."
                    )
                )
            else:
                llm_text = history_text = msg.text

            # Build messages array for SDK (includes history + new user message)
            messages: list[dict] = list(pool.sdk_history)
            messages.append({"role": "user", "content": history_text})

            # Build effective ModelConfig from overlay
            effective_cfg = ModelConfig(
                backend="anthropic-sdk",
                model=effective.model,
                max_turns=effective.max_turns,
                tools=self.config.model_config.tools,
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
        msg = self._handle_voice_command(msg) or msg
        return await self._process_llm(msg, pool, on_intermediate=on_intermediate)
