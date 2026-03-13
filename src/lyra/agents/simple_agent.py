"""
SimpleAgent — first concrete AgentBase implementation.

Wraps an LlmProvider to route messages through the configured backend.
Model and backend are read from the agent's TOML config (ModelConfig),
not hardcoded here.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from lyra.core.agent import _AGENTS_DIR, Agent, AgentBase
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
    from lyra.stt import STTService

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
        admin_user_ids: set[str] | None = None,
        msg_manager: MessageManager | None = None,
        stt: STTService | None = None,
        runtime_config: RuntimeConfig | None = None,
        agents_dir: Path | None = None,
    ) -> None:
        resolved_agents_dir = agents_dir or _AGENTS_DIR
        rc = (
            runtime_config
            if runtime_config is not None
            else RuntimeConfig.load(resolved_agents_dir / "lyra_runtime.toml")
        )
        self._runtime_config_holder = RuntimeConfigHolder(rc)
        self._runtime_config_path = resolved_agents_dir / "lyra_runtime.toml"
        super().__init__(
            config,
            agents_dir=agents_dir,
            circuit_registry=circuit_registry,
            admin_user_ids=admin_user_ids,
            msg_manager=msg_manager,
            stt=stt,
        )
        self._provider = provider

    def _build_router_kwargs(self) -> dict[str, object]:
        return {
            "runtime_config_holder": self._runtime_config_holder,
            "runtime_config_path": self._runtime_config_path,
        }

    def _maybe_register_reset(self, pool: Pool) -> None:
        """Register a session reset callback on the pool the first time we process.

        /clear calls pool.reset_session(), which delegates here → CliPool.reset().
        """
        if pool._session_reset_fn is None:
            reset_fn = getattr(self._provider, "reset", None)
            if reset_fn is not None:
                _pool_id = pool.pool_id
                pool._session_reset_fn = lambda: reset_fn(_pool_id)

    async def process(self, msg: InboundMessage, pool: Pool) -> Response:
        self._maybe_reload()
        self._maybe_register_reset(pool)

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
            text = f"🎤 [transcribed]: {stt_result.text}"
        else:
            text = msg.text

        model_cfg = self.config.model_config

        log.debug(
            "[agent:%s][pool:%s] processing message (%d chars)",
            self.name,
            pool.pool_id,
            len(text),
        )

        result = await self._provider.complete(
            pool.pool_id, text, model_cfg, self.config.system_prompt
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

        return Response(content=reply, metadata=meta)
