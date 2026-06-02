"""AgentDefaultsConfig — frozen dataclass for agent-level LLM model defaults.

Single source of truth for default model identifiers used across ModelConfig
(ports/llm_types.py) and the TOML seeder (agent/agent_seeder.py).

Constraints
-----------
- stdlib only — no framework imports, no factory.* imports.
- frozen=True to prevent accidental mutation.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AgentDefaultsConfig:
    """Default values for agent LLM model configuration.

    DEFAULT_MODEL:        model used when no model is specified at the API boundary
                          (ModelConfig default).
    DEFAULT_SEEDER_MODEL: fallback model applied by the TOML seeder when no
                          [model].model key is present in the agent TOML file.
    """

    DEFAULT_MODEL: str = "claude-opus-4-6"
    DEFAULT_SEEDER_MODEL: str = "claude-3-5-haiku-20241022"


__all__ = ["AgentDefaultsConfig"]
