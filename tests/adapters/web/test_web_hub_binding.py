"""Hub binding resolution for web smoke agent picker."""

from __future__ import annotations

from datetime import datetime, timezone

from factory.core.auth.trust import TrustLevel
from factory.core.hub import Hub
from factory.core.messaging.message import InboundMessage, Platform, WebMeta


def _web_msg(agent: str) -> InboundMessage:
    return InboundMessage(
        id="1",
        platform="web",
        bot_id="smoke",
        scope_id=f"agent:{agent}",
        user_id="smoke",
        user_name="Smoke",
        is_mention=True,
        text="ping",
        text_raw="ping",
        timestamp=datetime.now(timezone.utc),
        platform_meta=WebMeta(session_id="s1"),
        trust_level=TrustLevel.TRUSTED,
    )


class TestWebHubBinding:
    def test_resolve_binding_per_agent(self) -> None:
        hub = Hub()
        hub.register_binding(
            Platform.WEB,
            "smoke",
            "agent:lyra_default",
            "lyra_default",
            "web:smoke:agent:lyra_default",
        )
        binding = hub.resolve_binding(_web_msg("lyra_default"))
        assert binding is not None
        assert binding.agent_name == "lyra_default"

    def test_unknown_agent_scope_returns_none(self) -> None:
        hub = Hub()
        hub.register_binding(
            Platform.WEB,
            "smoke",
            "agent:lyra_default",
            "lyra_default",
            "web:smoke:agent:lyra_default",
        )
        assert hub.resolve_binding(_web_msg("missing")) is None
