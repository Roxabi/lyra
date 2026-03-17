from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .command_router import CommandConfig

# Agent config directories — resolution order: user → system
_USER_AGENTS_DIR = Path.home() / ".lyra" / "agents"
_SYSTEM_AGENTS_DIR = Path(__file__).resolve().parent.parent / "agents"
_AGENTS_DIR = _SYSTEM_AGENTS_DIR  # backward-compat alias (tests import this)
AGENTS_DIR = _SYSTEM_AGENTS_DIR  # public alias (system defaults)

_VALID_BACKENDS: frozenset[str] = frozenset({"claude-cli", "ollama", "anthropic-sdk"})
_MAX_PROMPT_BYTES = 64 * 1024  # 64 KB

_WORKSPACE_BUILTIN_CONFLICTS = frozenset(
    {
        "help",
        "circuit",
        "routing",
        "stop",
        "config",
        "clear",
        "new",
        "workspace",  # /workspace is now a builtin command
        # Session command names — a workspace with these names would silently
        # conflict with the session command registered in _register_session_commands().
        "add",
        "explain",
        "summarize",
        "search",
    }
)


def _find_agent_dir(name: str, agents_dir: Path | None) -> Path:
    """Resolve the directory that owns <name>.toml.

    Resolution order:
        1. Explicit agents_dir (tests / overrides)
        2. ~/.lyra/agents/   — user-level config (gitignored, machine-specific)
        3. src/lyra/agents/  — system defaults (versioned)
    """
    if agents_dir is not None:
        return agents_dir
    if (_USER_AGENTS_DIR / f"{name}.toml").exists():
        return _USER_AGENTS_DIR
    return _SYSTEM_AGENTS_DIR


@dataclass(frozen=True)
class ModelConfig:
    """Per-agent model configuration.

    backend: execution backend — "claude-cli" (Claude Code subscription)
             or "ollama" (local, future).
    model:   model identifier passed to the backend CLI.
    max_turns: max agentic turns per conversation turn.
    tools:   allowed tools (empty = backend defaults).
    cwd:     working directory for the Claude subprocess (claude-cli only).
             None → defaults to the Lyra project root.
             Useful to point a dedicated agent at another project so it reads
             that project's CLAUDE.md and has access to its files.

    This will evolve into an intelligent model selection system.
    """

    backend: str = "claude-cli"
    model: str = "claude-sonnet-4-5"
    max_turns: int = 10
    tools: tuple[str, ...] = field(default=())
    # compare=False: cwd is spawn-routing config, not model identity.
    # Changing cwd should not trigger the "model_config mismatch" warning
    # in CliPool.send() — that check is for backend/model/tools changes only.
    cwd: Path | None = field(default=None, compare=False)
    skip_permissions: bool = False
    streaming: bool = False


class Complexity(Enum):
    """Message complexity levels for routing decisions (#134)."""

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


@dataclass(frozen=True)
class SmartRoutingConfig:
    """Configuration for the smart routing decorator (#134)."""

    enabled: bool = False
    routing_table: dict[Complexity, str] = field(default_factory=dict)
    history_size: int = 50
    high_complexity_commands: tuple[str, ...] = ()


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


@dataclass(frozen=True)
class AgentTTSConfig:
    """Per-agent TTS defaults parsed from [tts] section in agent TOML.

    All fields optional — None means use voicecli global defaults.
    Merged into TTSConfig at startup in __main__.py.
    """

    engine: str | None = None
    voice: str | None = None
    language: str | None = None
    accent: str | None = None
    personality: str | None = None
    speed: str | None = None
    emotion: str | None = None
    segment_gap: int | None = None
    crossfade: int | None = None
    chunked: bool | None = None
    chunk_size: int | None = None


@dataclass(frozen=True)
class AgentSTTConfig:
    """Per-agent STT detection params parsed from [stt] section in agent TOML.

    All fields optional — None means use voicecli global defaults.
    Merged into STTConfig at startup in __main__.py.
    """

    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None


@dataclass
class Agent:
    """Configuration record for an agent. Mutable for hot-reload."""

    name: str
    system_prompt: str
    memory_namespace: str
    model_config: ModelConfig = field(default_factory=ModelConfig)
    permissions: tuple[str, ...] = field(default=())
    commands: dict[str, CommandConfig] = field(default_factory=dict)
    plugins_enabled: tuple[str, ...] = field(default=())  # empty = default-open
    persona: PersonaConfig | None = None
    i18n_language: str = "en"
    smart_routing: SmartRoutingConfig | None = None
    show_intermediate: bool = False  # show ⏳-prefixed intermediate turns to the user
    workspaces: dict[str, Path] = field(default_factory=dict)
    tts: AgentTTSConfig | None = None
    stt: AgentSTTConfig | None = None
