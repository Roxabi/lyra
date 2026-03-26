"""Tests for smart routing TOML config parsing (#134)."""

from __future__ import annotations

import tomllib

from lyra.core.agent_config import Complexity, SmartRoutingConfig


class TestSmartRoutingTomlConfig:
    def test_routing_table_parsed_from_toml(self) -> None:
        """All four complexity levels are parsed from TOML."""
        # Arrange
        toml_str = b"""
[agent]
name = "test"
memory_namespace = "test"

[model]
backend = "anthropic-sdk"
model = "claude-sonnet-4-6"

[agent.smart_routing]
enabled = true

[agent.smart_routing.models]
trivial  = "claude-haiku-4-5-20251001"
simple   = "claude-haiku-4-5-20251001"
moderate = "claude-sonnet-4-6"
complex  = "claude-opus-4-6"
"""
        data = tomllib.loads(toml_str.decode())

        # Act — replicate the parsing logic from load_agent_config
        agent_section = data["agent"]
        sr_section = agent_section.get("smart_routing")
        assert sr_section is not None
        sr_models = sr_section.get("models", {})
        routing_table: dict[Complexity, str] = {}
        for level in Complexity:
            model_id = sr_models.get(level.value)
            if model_id:
                routing_table[level] = model_id
        config = SmartRoutingConfig(
            enabled=bool(sr_section.get("enabled", False)),
            routing_table=routing_table,
        )

        # Assert
        assert config.enabled is True
        assert len(config.routing_table) == 4
        assert config.routing_table[Complexity.TRIVIAL] == "claude-haiku-4-5-20251001"
        assert config.routing_table[Complexity.SIMPLE] == "claude-haiku-4-5-20251001"
        assert config.routing_table[Complexity.MODERATE] == "claude-sonnet-4-6"
        assert config.routing_table[Complexity.COMPLEX] == "claude-opus-4-6"


def test_routing_table_json_roundtrip() -> None:
    cfg = SmartRoutingConfig(
        enabled=True,
        routing_table={Complexity.TRIVIAL: "haiku", Complexity.COMPLEX: "opus"},
    )
    dumped = cfg.model_dump(mode="json")
    assert isinstance(list(dumped["routing_table"].keys())[0], str)
    restored = SmartRoutingConfig.model_validate(dumped)
    assert restored.routing_table[Complexity.TRIVIAL] == "haiku"
    assert restored.routing_table[Complexity.COMPLEX] == "opus"
