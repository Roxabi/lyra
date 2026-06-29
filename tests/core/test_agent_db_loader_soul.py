"""Tests for agent_row_to_config soul cache + persona_json fallback."""

from __future__ import annotations

import json

from factory.core.agent.agent_db_loader import agent_row_to_config
from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache


class TestAgentRowSoulResolution:
    def setup_method(self) -> None:
        get_soul_document_cache().invalidate("lyra")

    def test_cache_hit_composes_from_soul(self) -> None:
        md = "## Identity\nLyra bot\n"
        get_soul_document_cache().put(
            "lyra",
            blob_ref="sha256:abc",
            markdown=md,
            composed_prompt="## Identity\nLyra bot\n\n## Voice messages\nWhen",
        )
        row = AgentRow(
            name="lyra",
            backend="claude-cli",
            model="sonnet",
            soul_document_blob_ref="sha256:abc",
        )
        agent = agent_row_to_config(row)
        assert "Lyra bot" in agent.system_prompt

    def test_fallback_persona_json_on_cache_miss(self) -> None:
        persona = json.dumps({"identity": {"display_name": "Legacy"}})
        row = AgentRow(
            name="lyra",
            backend="claude-cli",
            model="sonnet",
            persona_json=persona,
            soul_document_blob_ref="sha256:missing",
        )
        agent = agent_row_to_config(row)
        assert "Legacy" in agent.system_prompt