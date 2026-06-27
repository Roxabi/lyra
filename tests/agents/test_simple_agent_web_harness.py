"""Per-tab web harness/model overrides in SimpleAgent (#1771)."""

from __future__ import annotations

from unittest.mock import MagicMock

from factory.agents.simple_agent import SimpleAgent
from factory.core.agent import Agent
from factory.core.messaging.message import InboundMessage, WebMeta
from factory.core.ports.llm_types import ModelConfig


def _web_msg(*, harness: str | None = None, model: str | None = None) -> InboundMessage:
    return InboundMessage(
        id="1",
        platform="web",
        bot_id="smoke",
        scope_id="agent:lyra",
        user_id="u",
        user_name="u",
        is_mention=True,
        text="hi",
        text_raw="hi",
        trust_level=MagicMock(),
        platform_meta=WebMeta(session_id="s1", harness=harness, model=model),
    )


def test_effective_model_config_applies_web_tab_overrides() -> None:
    agent_cfg = Agent(
        name="lyra",
        system_prompt="test",
        memory_namespace="lyra",
        llm_config=ModelConfig(backend="claude-cli", model="claude-opus-4-6"),
    )
    agent = SimpleAgent(agent_cfg, provider=MagicMock())
    cfg = agent._effective_model_config(
        _web_msg(harness="omp-rpc", model="omp-fast")
    )
    assert cfg.backend == "omp-rpc"
    assert cfg.model == "omp-fast"