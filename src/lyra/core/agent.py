from __future__ import annotations

import re
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from .message import Message, Response
from .pool import Pool

# Default agents config directory: src/lyra/agents/
_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"


@dataclass(frozen=True)
class ModelConfig:
    """Per-agent model configuration.

    backend: execution backend — "claude-cli" (Claude Code subscription)
             or "ollama" (local, future).
    model:   model identifier passed to the backend CLI.
    max_turns: max agentic turns per conversation turn.
    tools:   allowed tools (empty = backend defaults).

    This will evolve into an intelligent model selection system.
    """

    backend: str = "claude-cli"
    model: str = "claude-sonnet-4-5"
    max_turns: int = 10
    tools: tuple[str, ...] = field(default=())


@dataclass(frozen=True)
class Agent:
    """Immutable configuration record for an agent."""

    name: str
    system_prompt: str
    memory_namespace: str
    model_config: ModelConfig = field(default_factory=ModelConfig)
    permissions: tuple[str, ...] = field(default=())


def load_agent_config(name: str, agents_dir: Path | None = None) -> Agent:
    """Load an Agent from a TOML config file.

    Looks for <agents_dir>/<name>.toml.
    Falls back to _AGENTS_DIR if agents_dir is not specified.

    TOML structure:
        [agent]
        memory_namespace = "lyra"
        permissions = []

        [model]
        backend = "claude-cli"
        model = "claude-sonnet-4-5"
        max_turns = 10
        tools = []

        [prompt]
        system = "..."
    """
    directory = agents_dir or _AGENTS_DIR

    # validate name before path construction
    if not re.match(r'^[a-zA-Z0-9_-]+$', name):
        raise ValueError(f"Invalid agent name {name!r}: only [a-zA-Z0-9_-] allowed")

    path = directory / f"{name}.toml"
    # Guard against path traversal
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Agent name {name!r} escapes agents directory")
    if not path.exists():
        raise FileNotFoundError(f"Agent config not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    agent_section = data.get("agent", {})

    # Validate declared name matches the filename stem (if declared)
    declared_name = agent_section.get("name")
    if declared_name is not None and declared_name != name:
        raise ValueError(
            f"Agent name mismatch: file {name!r} declares name {declared_name!r}"
        )

    model_section = data.get("model", {})
    prompt_section = data.get("prompt", {})

    model_cfg = ModelConfig(
        backend=model_section.get("backend", "claude-cli"),
        model=model_section.get("model", "claude-sonnet-4-5"),
        max_turns=int(model_section.get("max_turns", 10)),
        tools=tuple(model_section.get("tools", [])),
    )

    return Agent(
        name=name,
        system_prompt=prompt_section.get("system", ""),
        memory_namespace=agent_section.get("memory_namespace", name),
        model_config=model_cfg,
        permissions=tuple(agent_section.get("permissions", [])),
    )


class AgentBase(ABC):
    """Abstract base for concrete agent implementations.

    All mutable state lives in Pool.
    """

    def __init__(self, config: Agent) -> None:
        self.config = config

    @property
    def name(self) -> str:
        return self.config.name

    @abstractmethod
    async def process(self, msg: Message, pool: Pool) -> Response: ...
