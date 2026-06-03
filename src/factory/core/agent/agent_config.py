from __future__ import annotations

from enum import Enum
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, field_serializer, field_validator

from ..commands.command_router import CommandConfig
from ..config.limits import (
    MAX_PROMPT_BYTES as MAX_PROMPT_BYTES,  # noqa: F401 — re-export for callers
)

# ModelConfig is the canonical LLM port value type — defined in core/ports/llm_types.py
# so that llm.py (the driven port) is self-contained. Re-exported here for
# backward compatibility with all callers that import from agent_config.
from ..ports.llm_types import ModelConfig as ModelConfig  # noqa: F401

_VALID_BACKENDS: frozenset[str] = frozenset({"claude-cli", "nats"})

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
    history_size: int = 50  # const-ok: SmartRouting rolling window, single SSoT
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

    All fields optional — None means use global defaults.
    Passed per-call to TtsProtocol.synthesize() at voice generation time.
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

    All fields optional — None means use global defaults.
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
    workspaces: dict[str, Path] = {}
    voice: AgentVoiceConfig | None = None  # #343 — unified voice config
    patterns: dict[str, bool] = {}  # #345 — rewrite rules
    passthroughs: tuple[str, ...] = ()  # commands forwarded to LLM
