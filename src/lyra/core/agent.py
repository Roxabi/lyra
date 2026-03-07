from __future__ import annotations

import logging
import os
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

_VALID_BACKENDS: frozenset[str] = frozenset({"claude-cli", "ollama", "anthropic-sdk"})
_MAX_PROMPT_BYTES = 64 * 1024  # 64 KB

_VAULT_DIR = Path(
    os.environ.get("ROXABI_VAULT_DIR", str(Path.home() / ".roxabi-vault"))
)
_PERSONAS_DIR = _VAULT_DIR / "personas"


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
class IdentityConfig:
    name: str
    tagline: str = ""
    creator: str = ""
    role: str = ""
    goal: str = ""


@dataclass(frozen=True)
class PersonalityConfig:
    traits: tuple[str, ...] = ()
    communication_style: str = ""
    tone: str = ""
    humor: str = ""


@dataclass(frozen=True)
class ExpertiseConfig:
    areas: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoiceConfig:
    speaking_style: str = ""
    pace: str = ""
    warmth: str = ""


@dataclass(frozen=True)
class PersonaConfig:
    identity: IdentityConfig
    personality: PersonalityConfig = field(default_factory=PersonalityConfig)
    expertise: ExpertiseConfig = field(default_factory=ExpertiseConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)


@dataclass
class Agent:
    """Configuration record for an agent. Mutable for hot-reload."""

    name: str
    system_prompt: str
    memory_namespace: str
    model_config: ModelConfig = field(default_factory=ModelConfig)
    permissions: tuple[str, ...] = field(default=())
    commands: dict[str, CommandConfig] = field(default_factory=dict)
    persona: PersonaConfig | None = None


def load_persona(name: str, personas_dir: Path | None = None) -> PersonaConfig:
    """Load PersonaConfig from a TOML file in the vault.

    Resolves name to {personas_dir}/{name}.persona.toml.
    Validates: name safe, file exists, [identity].name present.
    Creates personas_dir if absent.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"Invalid persona name {name!r}: only [a-zA-Z0-9_-] allowed")

    directory = personas_dir or _PERSONAS_DIR
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{name}.persona.toml"
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Persona name {name!r} escapes personas directory")
    if not path.exists():
        raise FileNotFoundError(f"Persona config not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    identity_data = data.get("identity", {})
    if not identity_data.get("name"):
        raise ValueError(f"Persona {name!r} missing required [identity].name")

    identity = IdentityConfig(
        name=identity_data["name"],
        tagline=identity_data.get("tagline", ""),
        creator=identity_data.get("creator", ""),
        role=identity_data.get("role", ""),
        goal=identity_data.get("goal", ""),
    )

    personality_data = data.get("personality", {})
    personality = PersonalityConfig(
        traits=tuple(personality_data.get("traits", [])),
        communication_style=personality_data.get("communication_style", ""),
        tone=personality_data.get("tone", ""),
        humor=personality_data.get("humor", ""),
    )

    expertise_data = data.get("expertise", {})
    expertise = ExpertiseConfig(
        areas=tuple(expertise_data.get("areas", [])),
        instructions=tuple(expertise_data.get("instructions", [])),
    )

    voice_data = data.get("voice", {})
    voice = VoiceConfig(
        speaking_style=voice_data.get("speaking_style", ""),
        pace=voice_data.get("pace", ""),
        warmth=voice_data.get("warmth", ""),
    )

    return PersonaConfig(
        identity=identity,
        personality=personality,
        expertise=expertise,
        voice=voice,
    )


def compose_system_prompt(persona: PersonaConfig) -> str:
    """Build a natural prose system prompt from PersonaConfig fields."""
    parts: list[str] = []

    # Identity paragraph
    ident = persona.identity
    intro = f"You are {ident.name}"
    if ident.tagline:
        intro += f", {ident.tagline}"
    if ident.creator:
        intro += f", created by {ident.creator}"
    intro += "."
    if ident.goal:
        intro += f" {ident.goal}"
    parts.append(intro)

    # Personality paragraph
    p = persona.personality
    if p.traits or p.communication_style or p.tone:
        personality_parts: list[str] = []
        if p.traits:
            personality_parts.append(f"Your core traits are: {', '.join(p.traits)}.")
        if p.communication_style:
            personality_parts.append(
                f"Your communication style is {p.communication_style}."
            )
        if p.tone:
            personality_parts.append(f"Your tone is {p.tone}.")
        if p.humor:
            personality_parts.append(f"Your sense of humor: {p.humor}.")
        parts.append(" ".join(personality_parts))

    # Expertise paragraph
    e = persona.expertise
    if e.areas:
        parts.append(f"Your areas of expertise include: {', '.join(e.areas)}.")
    if e.instructions:
        instruction_lines = "\n".join(f"- {i}" for i in e.instructions)
        parts.append(f"Guidelines:\n{instruction_lines}")

    composed = "\n\n".join(parts)

    encoded = composed.encode()
    if len(encoded) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"Composed system prompt exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded)} bytes)"
        )

    return composed


def load_agent_config(
    name: str,
    agents_dir: Path | None = None,
    personas_dir: Path | None = None,
) -> Agent:
    """Load an Agent from a TOML config file.

    Looks for <agents_dir>/<name>.toml.
    Falls back to _AGENTS_DIR if agents_dir is not specified.

    TOML structure:
        [agent]
        memory_namespace = "lyra"
        permissions = []
        persona = "lyra_default"   # optional: loads from vault

        [model]
        backend = "claude-cli"
        model = "claude-sonnet-4-5"
        max_turns = 10
        tools = []

        [prompt]
        system = "..."             # optional: overrides persona composition
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

    # Persona loading
    persona_name = agent_section.get("persona")
    persona: PersonaConfig | None = None
    if persona_name:
        persona = load_persona(persona_name, personas_dir)

    system_prompt = prompt_section.get("system", "")
    if not system_prompt and persona:
        system_prompt = compose_system_prompt(persona)

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
            timeout=float(cmd_data.get("timeout", 30.0)),
        )

    return Agent(
        name=name,
        system_prompt=system_prompt,
        memory_namespace=agent_section.get("memory_namespace", name),
        model_config=model_cfg,
        permissions=tuple(agent_section.get("permissions", [])),
        commands=commands,
        persona=persona,
    )


class AgentBase(ABC):
    """Abstract base for concrete agent implementations.

    All mutable state lives in Pool.
    Supports hot-reload: edit the TOML or persona file and config updates
    on next message.
    """

    def __init__(self, config: Agent, agents_dir: Path | None = None) -> None:
        self.config = config
        self._agents_dir = agents_dir or _AGENTS_DIR
        self._config_path = self._agents_dir / f"{config.name}.toml"
        self._last_mtime: float = (
            self._config_path.stat().st_mtime if self._config_path.exists() else 0.0
        )
        self.command_router: CommandRouter = CommandRouter(config.commands)
        self._persona_path: Path | None = None
        self._persona_mtime: float = 0.0
        self._update_persona_tracking()

    def _update_persona_tracking(self) -> None:
        """Resolve and track persona vault file for hot-reload."""
        if self.config.persona is None:
            self._persona_path = None
            self._persona_mtime = 0.0
            return
        try:
            with self._config_path.open("rb") as f:
                data = tomllib.load(f)
            persona_name = data.get("agent", {}).get("persona", "")
            if persona_name:
                p_path = _PERSONAS_DIR / f"{persona_name}.persona.toml"
                self._persona_path = p_path
                self._persona_mtime = p_path.stat().st_mtime if p_path.exists() else 0.0
        except Exception:
            pass

    @property
    def name(self) -> str:
        return self.config.name

    def _maybe_reload(self) -> None:
        """Reload config from TOML if the agent or persona file changed."""
        try:
            mtime = self._config_path.stat().st_mtime
        except OSError:
            return

        config_changed = mtime > self._last_mtime
        persona_changed = False

        if self._persona_path:
            try:
                p_mtime = self._persona_path.stat().st_mtime
                persona_changed = p_mtime > self._persona_mtime
            except OSError:
                pass

        if not config_changed and not persona_changed:
            return

        try:
            new_config = load_agent_config(self.config.name, self._agents_dir)
            if new_config != self.config:
                log.info(
                    "Hot-reloaded config for agent %r (model: %s -> %s)",
                    self.config.name,
                    self.config.model_config.model,
                    new_config.model_config.model,
                )
                self.config = new_config
                self.command_router = CommandRouter(new_config.commands)
            self._last_mtime = mtime
            self._update_persona_tracking()
        except Exception as exc:
            log.warning("Failed to reload config for %r: %s", self.config.name, exc)

    @abstractmethod
    async def process(self, msg: Message, pool: Pool) -> Response: ...
