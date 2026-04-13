from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_serializer, field_validator

from .commands.command_router import CommandConfig

_VALID_BACKENDS: frozenset[str] = frozenset(
    {"claude-cli", "ollama", "anthropic-sdk", "litellm"}
)
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


class ModelConfig(BaseModel):
    """Per-agent model configuration.

    backend: execution backend — "claude-cli" (Claude Code subscription),
             "ollama" (local, future), or "litellm" (unified LLM proxy).
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
    base_url: override the backend API base URL.
             e.g. "http://localhost:11434/v1" for Ollama local, None for cloud
             defaults. Used by litellm and anthropic-sdk backends.
    api_key: backend API key override. Resolved via secrets store at runtime;
             None = use env var (FIREWORKS_API_KEY, ANTHROPIC_API_KEY, etc.).
             Intentionally excluded from __eq__ and __hash__ — it is a
             credential, not part of model identity.

    This will evolve into an intelligent model selection system.
    """

    model_config = ConfigDict(frozen=True)

    backend: str = "claude-cli"
    model: str = "claude-opus-4-6"
    max_turns: int | None = None  # None = unlimited (0 sentinel in DB)
    tools: tuple[str, ...] = ()
    # cwd is spawn-routing config, not model identity.
    # Changing cwd should not trigger the "model_config mismatch" warning
    # in CliPool.send() — that check is for backend/model/tools changes only.
    cwd: Path | None = None
    skip_permissions: bool = False
    streaming: bool = False
    base_url: str | None = None
    api_key: str | None = Field(default=None, exclude=True, repr=False)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, ModelConfig):
            return NotImplemented
        return (
            self.backend == other.backend
            and self.model == other.model
            and self.max_turns == other.max_turns
            and self.tools == other.tools
            and self.skip_permissions == other.skip_permissions
            and self.streaming == other.streaming
            and self.base_url == other.base_url
            # cwd excluded — spawn-routing config, not model identity
            # api_key excluded — credential, not model identity
        )

    def __hash__(self) -> int:
        return hash(
            (
                self.backend,
                self.model,
                self.max_turns,
                self.tools,
                self.skip_permissions,
                self.streaming,
                self.base_url,
                # cwd intentionally excluded
                # api_key intentionally excluded — credential, not model identity
            )
        )


class Complexity(Enum):
    """Message complexity levels for routing decisions (#134)."""

    TRIVIAL = "trivial"
    SIMPLE = "simple"
    MODERATE = "moderate"
    COMPLEX = "complex"


class SmartRoutingConfig(BaseModel):
    """Configuration for the smart routing decorator (#134)."""

    model_config = ConfigDict(frozen=True)

    enabled: bool = False
    routing_table: dict[Complexity, str] = {}
    history_size: int = 50
    high_complexity_commands: tuple[str, ...] = ()

    @field_validator("routing_table", mode="before")
    @classmethod
    def _coerce_routing_table_keys(cls, v: Any) -> dict[Complexity, str]:
        """Convert string keys to Complexity enum values when loading from JSON."""
        if not isinstance(v, dict):
            return v
        result: dict[Complexity, str] = {}
        for key, val in v.items():
            if isinstance(key, str):
                result[Complexity(key)] = val
            else:
                result[key] = val
        return result

    @field_serializer("routing_table")
    def _serialize_routing_table(self, v: dict[Complexity, str]) -> dict[str, str]:
        """Serialize Complexity enum keys to their .value for JSON output."""
        return {k.value: val for k, val in v.items()}


class AgentTTSConfig(BaseModel):
    """Per-agent TTS defaults.

    All fields optional — None means use voicecli global defaults.
    Passed per-call to TTSService.synthesize() at voice generation time.
    """

    model_config = ConfigDict(frozen=True)

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
    languages: list[str] | None = None  # detection candidates, e.g. ["fr", "en"]
    default_language: str | None = None  # fallback when detection fails


class AgentSTTConfig(BaseModel):
    """Per-agent STT detection params.

    All fields optional — None means use voicecli global defaults.
    Merged into STTConfig at startup in __main__.py.
    """

    model_config = ConfigDict(frozen=True)

    language_detection_threshold: float | None = None
    language_detection_segments: int | None = None
    language_fallback: str | None = None


class AgentVoiceConfig(BaseModel):
    """Unified per-agent voice config wrapping TTS + STT typed configs (#343)."""

    model_config = ConfigDict(frozen=True)

    tts: AgentTTSConfig = AgentTTSConfig()
    stt: AgentSTTConfig = AgentSTTConfig()


class Agent(BaseModel):
    """Configuration record for an agent. Mutable for hot-reload."""

    model_config = ConfigDict(frozen=False)

    name: str
    system_prompt: str
    memory_namespace: str
    # RENAMED from model_config to llm_config: Pydantic reserves 'model_config'
    # for ConfigDict. All callers must use .llm_config instead of .model_config.
    llm_config: ModelConfig = ModelConfig()
    permissions: tuple[str, ...] = ()
    commands: dict[str, "CommandConfig"] = {}
    commands_enabled: tuple[str, ...] = ()  # empty = default-open
    i18n_language: str = "en"
    smart_routing: SmartRoutingConfig | None = None
    show_intermediate: bool = False  # show ⏳-prefixed intermediate turns to the user
    show_tool_recap: bool = True  # show 🔧-prefixed tool summary card after tool use
    workspaces: dict[str, Path] = {}
    voice: AgentVoiceConfig | None = None  # #343 — unified voice config
    patterns: dict[str, bool] = {}  # #345 — rewrite rules
    passthroughs: tuple[str, ...] = ()  # commands forwarded to LLM
