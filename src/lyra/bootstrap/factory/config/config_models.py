"""Pydantic config section models (#411)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class CliPoolConfig(BaseModel):
    """Typed [cli_pool] config section."""

    model_config = ConfigDict(frozen=True)

    idle_ttl: int = 1200
    default_timeout: int = 1200
    turn_timeout: float | None = None
    reaper_interval: int = 60
    kill_timeout: float = 5.0
    read_buffer_bytes: int = 1024 * 1024
    stdin_drain_timeout: float = 10.0
    max_idle_retries: int = 3
    intermediate_timeout: float = 5.0


class HubConfig(BaseModel):
    """Typed [hub] config section."""

    model_config = ConfigDict(frozen=True)

    pool_ttl: float = 604800.0
    rate_limit: int = 20
    rate_window: int = 60


class PoolConfig(BaseModel):
    """Typed [pool] config section."""

    model_config = ConfigDict(frozen=True)

    safe_dispatch_timeout: float = 10.0


class LlmConfig(BaseModel):
    """Typed [llm] config section."""

    model_config = ConfigDict(frozen=True)

    max_retries: int = 3
    backoff_base: float = 1.0


class InboundBusConfig(BaseModel):
    """Typed [inbound_bus] config section."""

    model_config = ConfigDict(frozen=True)

    queue_depth_threshold: int = 100
    staging_maxsize: int = 500
    platform_queue_maxsize: int = 100


class DebouncerConfig(BaseModel):
    """Typed [debouncer] config section."""

    model_config = ConfigDict(frozen=True)

    default_debounce_ms: int = 300
    max_merged_chars: int = 4096
    cancel_on_new_message: bool = False


class LoggingConfig(BaseModel):
    """Typed [logging] config section (#270)."""

    model_config = ConfigDict(frozen=True)

    level: str = "info"


class EventBusConfig(BaseModel):
    """Typed [event_bus] config section (#432)."""

    model_config = ConfigDict(frozen=True)

    queue_maxsize: int = 1000


class MessageIndexConfig(BaseModel):
    """Typed [message_index] config section (#417)."""

    model_config = ConfigDict(frozen=True)

    retention_days: int = 90


class AgentOverrideConfig(BaseModel):
    """Typed output of _build_agent_overrides().

    Uses extra="ignore" — only cwd, persona, workspaces are consumed; extra keys
    are silently discarded.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    cwd: str | None = None
    persona: str | None = None
    workspaces: dict[str, str] = {}
