"""Unit tests for bot_display_name()."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock

from factory.core.auth.trust import TrustLevel
from factory.core.messaging.bot_display_name import bot_display_name
from factory.core.messaging.message import InboundMessage, TelegramMeta


def _msg(bot_id: str = "lyra") -> InboundMessage:
    return InboundMessage(
        id="msg-1",
        platform="telegram",
        bot_id=bot_id,
        scope_id="chat:42",
        user_id="alice",
        user_name="Alice",
        is_mention=False,
        text="hello",
        text_raw="hello",
        timestamp=datetime.now(timezone.utc),
        platform_meta=TelegramMeta(chat_id=42),
        trust_level=TrustLevel.TRUSTED,
    )


class TestBotDisplayName:
    def test_none_returns_factory_default(self) -> None:
        assert bot_display_name(None) == "factory"

    def test_no_hub_returns_bot_id(self) -> None:
        assert bot_display_name(_msg("lyra")) == "lyra"

    def test_empty_bot_id_returns_factory_default(self) -> None:
        msg = _msg("")
        assert bot_display_name(msg) == "factory"

    def test_persona_display_name_from_hub_binding(self) -> None:
        hub = MagicMock()
        binding = MagicMock(agent_name="lyra")
        hub.resolve_binding.return_value = binding
        agent = MagicMock()
        row = MagicMock(
            persona_json='{"identity": {"display_name": "Lyra Bot"}}'
        )
        agent._agent_store.get.return_value = row
        hub.get_agent.return_value = agent

        assert bot_display_name(_msg(), hub) == "Lyra Bot"

    def test_store_lookup_failure_falls_back_to_bot_id(self) -> None:
        hub = MagicMock()
        binding = MagicMock(agent_name="lyra")
        hub.resolve_binding.return_value = binding
        agent = MagicMock()
        agent._agent_store.get.side_effect = RuntimeError("store down")
        hub.get_agent.return_value = agent

        assert bot_display_name(_msg("lyra"), hub) == "lyra"

    def test_malformed_persona_json_falls_back_to_bot_id(self) -> None:
        hub = MagicMock()
        binding = MagicMock(agent_name="lyra")
        hub.resolve_binding.return_value = binding
        agent = MagicMock()
        row = MagicMock(persona_json="not-json")
        agent._agent_store.get.return_value = row
        hub.get_agent.return_value = agent

        assert bot_display_name(_msg("lyra"), hub) == "lyra"