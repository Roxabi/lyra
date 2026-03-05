from __future__ import annotations

import logging
import re
import tomllib
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path

from .command_router import CommandConfig, CommandRouter
from .message import Message, Response
from .pool import Pool

log = logging.getLogger(__name__)

# Default agents config directory: src/lyra/agents/
_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"

_VALID_BACKENDS: frozenset[str] = frozenset({"claude-cli", "ollama"})
_MAX_PROMPT_BYTES = 64 * 1024  # 64 KB


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


@dataclass
class Agent:
    """Configuration record for an agent. Mutable for hot-reload."""

    name: str
    system_prompt: str
    memory_namespace: str
    model_config: ModelConfig = field(default_factory=ModelConfig)
    permissions: tuple[str, ...] = field(default=())
    commands: dict[str, CommandConfig] = field(default_factory=dict)


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

    # Primary gate: only safe characters allowed in agent names
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"Invalid agent name {name!r}: only [a-zA-Z0-9_-] allowed")

    path = directory / f"{name}.toml"
    # Secondary gate (defence-in-depth): guard against symlinks or edge cases
    # in path resolution even though the regex above makes traversal impossible.
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

    backend = model_section.get("backend", "claude-cli")
    if backend not in _VALID_BACKENDS:
        raise ValueError(
            f"Invalid backend {backend!r} for agent {name!r}: "
            f"must be one of {sorted(_VALID_BACKENDS)}"
        )

    model = model_section.get("model", "claude-sonnet-4-5")
    if not re.match(r"^[a-zA-Z0-9_.:-]+$", model):
        raise ValueError(
            f"Invalid model {model!r} for agent {name!r}: "
            "only [a-zA-Z0-9_.:-] characters allowed"
        )

    model_cfg = ModelConfig(
        backend=backend,
        model=model,
        max_turns=int(model_section.get("max_turns", 10)),
        tools=tuple(model_section.get("tools", [])),
    )

    system_prompt = prompt_section.get("system", "")
    encoded_prompt = system_prompt.encode()
    if len(encoded_prompt) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"system_prompt for agent {name!r} exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded_prompt)} bytes)"
        )

    commands_section = data.get("commands", {})
    commands: dict[str, CommandConfig] = {}
    for cmd_name, cmd_data in commands_section.items():
        commands[cmd_name] = CommandConfig(
            skill=cmd_data.get("skill"),
            action=cmd_data.get("action"),
            cli=cmd_data.get("cli"),
            description=cmd_data.get("description", ""),
            builtin=bool(cmd_data.get("builtin", False)),
        )

    return Agent(
        name=name,
        system_prompt=system_prompt,
        memory_namespace=agent_section.get("memory_namespace", name),
        model_config=model_cfg,
        permissions=tuple(agent_section.get("permissions", [])),
        commands=commands,
    )


class AgentBase(ABC):
    """Abstract base for concrete agent implementations.

    All mutable state lives in Pool.
    Supports hot-reload: edit the TOML file and config updates on next message.
    """

    def __init__(self, config: Agent, agents_dir: Path | None = None) -> None:
        self.config = config
        self._agents_dir = agents_dir or _AGENTS_DIR
        self._config_path = self._agents_dir / f"{config.name}.toml"
        self._last_mtime: float = (
            self._config_path.stat().st_mtime if self._config_path.exists() else 0.0
        )
        self.command_router: CommandRouter = CommandRouter(config.commands)

    @property
    def name(self) -> str:
        return self.config.name

    def _maybe_reload(self) -> None:
        """Reload config from TOML if the file changed since last check."""
        try:
            mtime = self._config_path.stat().st_mtime
        except OSError:
            return
        if mtime <= self._last_mtime:
            return
        try:
            new_config = load_agent_config(self.config.name, self._agents_dir)
            self._last_mtime = mtime
            if new_config != self.config:
                log.info(
                    "Hot-reloaded config for agent %r (model: %s -> %s)",
                    self.config.name,
                    self.config.model_config.model,
                    new_config.model_config.model,
                )
                self.config = new_config
                self.command_router = CommandRouter(new_config.commands)
        except Exception as exc:
            log.warning("Failed to reload config for %r: %s", self.config.name, exc)

    @abstractmethod
    async def process(self, msg: Message, pool: Pool) -> Response: ...
