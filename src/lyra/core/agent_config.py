from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .commands.command_router import CommandConfig

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
        "vault-add",
        "explain",
        "summarize",
        "search",
    }
)


@dataclass(frozen=True)
class ModelConfig:
    """Per-agent model configuration.

    backend: execution backend — "claude-cli" (Claude Code subscription)
             or "ollama" (local, future).
    model:   model identifier passed to the backend CLI.
    max_turns: max agentic turns per conversation turn.
             None (or 0 in DB) means unlimited — the backend imposes no cap.
             Default is None (unlimited). Set an explicit positive integer to
             throttle long-running agents.
    tools:   allowed tools (empty = backend defaults).
    cwd:     working directory for the Claude subprocess (claude-cli only).
             None → defaults to the Lyra project root.
             Useful to point a dedicated agent at another project so it reads
             that project's CLAUDE.md and has access to its files.

    This will evolve into an intelligent model selection system.
    """

    backend: str = "claude-cli"
    model: str = "claude-sonnet-4-5"
    max_turns: int | None = None  # None = unlimited (0 sentinel in DB)
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
class AgentTTSConfig:
    """Per-agent TTS defaults.

    All fields optional — None means use voicecli global defaults.
    Passed per-call to TTSService.synthesize() at voice generation time.
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
    exaggeration: float | None = None
    cfg_weight: float | None = None


@dataclass(frozen=True)
class AgentSTTConfig:
    """Per-agent STT detection params.

    All fields optional — None means use voicecli global defaults.
    Merged into STTConfig at startup in __main__.py.
    """

    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None


@dataclass(frozen=True)
class AgentVoiceConfig:
    """Unified per-agent voice config wrapping TTS + STT typed configs (#343)."""

    tts: AgentTTSConfig = field(default_factory=AgentTTSConfig)
    stt: AgentSTTConfig = field(default_factory=AgentSTTConfig)


@dataclass
class Agent:
    """Configuration record for an agent. Mutable for hot-reload."""

    name: str
    system_prompt: str
    memory_namespace: str
    model_config: ModelConfig = field(default_factory=ModelConfig)
    permissions: tuple[str, ...] = field(default=())
    commands: dict[str, CommandConfig] = field(default_factory=dict)
    commands_enabled: tuple[str, ...] = field(default=())  # empty = default-open
    i18n_language: str = "en"
    smart_routing: SmartRoutingConfig | None = None
    show_intermediate: bool = False  # show ⏳-prefixed intermediate turns to the user
    show_tool_recap: bool = True  # show 🔧-prefixed tool summary card after tool use
    workspaces: dict[str, Path] = field(default_factory=dict)
    voice: AgentVoiceConfig | None = None  # #343 — unified voice config
    patterns: dict[str, bool] = field(default_factory=dict)  # #345 — rewrite rules
    passthroughs: tuple[str, ...] = field(default=())  # commands forwarded to LLM
