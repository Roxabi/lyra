"""Monitoring configuration: thresholds from TOML, secrets from env vars."""

from __future__ import annotations

import os
import re
import tomllib
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

_HHMM_RE = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")
_SERVICE_NAME_RE = re.compile(r"^[a-zA-Z0-9_@.\-]+$")


class MonitoringConfig(BaseModel):
    """Configuration for the monitoring system.

    Thresholds come from [monitoring] section in lyra.toml.
    Secrets come from environment variables.
    """

    model_config = ConfigDict(frozen=True)

    # Thresholds (from TOML)
    check_interval_minutes: int = 5
    health_endpoint_timeout_s: int = 5
    queue_depth_threshold: int = 80
    idle_threshold_hours: int = 6
    quiet_start: str = "00:00"
    quiet_end: str = "08:00"
    idle_check_enabled: bool = False
    min_disk_free_gb: int = 1
    health_endpoint_url: str = "http://localhost:8443/health/detail"
    diagnostic_model: str = "claude-haiku-4-5-20251001"
    disk_check_path: str = "/"
    service_name: str = "lyra"

    # Secrets (from env vars)
    telegram_token: str = Field(default="", repr=False)
    anthropic_api_key: str = Field(default="", repr=False)
    telegram_admin_chat_id: str = Field(default="", repr=False)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _validate_hhmm(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError(f"must be HH:MM format, got {v!r}")
        return v

    @field_validator("service_name")
    @classmethod
    def _validate_service_name(cls, v: str) -> str:
        if not _SERVICE_NAME_RE.match(v):
            raise ValueError(f"service_name must match [a-zA-Z0-9_@.-]+, got {v!r}")
        return v

    @field_validator("health_endpoint_url")
    @classmethod
    def _validate_health_endpoint_url(cls, v: str) -> str:
        parsed = urlparse(v)
        if parsed.scheme not in ("http", "https"):
            raise ValueError(
                f"health_endpoint_url must use http or https scheme, "
                f"got {parsed.scheme!r}"
            )
        return v

    @model_validator(mode="after")
    def _validate_quiet_times(self) -> MonitoringConfig:
        # Both fields are already validated individually; model_validator runs after.
        # This hook is a placeholder for any cross-field validation needed in future.
        return self


def load_monitoring_config(config_path: str | None = None) -> MonitoringConfig:
    """Load monitoring config from TOML thresholds + env var secrets.

    Config path resolution: config_path arg → $LYRA_CONFIG → lyra.toml in cwd.
    Missing config file → all defaults for thresholds.
    Missing required env vars → ValueError.
    """
    # Load TOML thresholds
    path = config_path or os.environ.get("LYRA_CONFIG", "lyra.toml")
    raw: dict[str, object] = {}
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        pass

    monitoring_raw = raw.get("monitoring", {})
    monitoring_section: dict[str, object] = (
        monitoring_raw if isinstance(monitoring_raw, dict) else {}
    )

    # Load secrets from env vars
    telegram_token = os.environ.get("TELEGRAM_TOKEN", "")
    anthropic_api_key = os.environ.get("ANTHROPIC_API_KEY", "")
    telegram_admin_chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")

    if not telegram_token:
        raise ValueError(
            "TELEGRAM_TOKEN environment variable is required for monitoring"
        )
    if not anthropic_api_key:
        raise ValueError(
            "ANTHROPIC_API_KEY environment variable is required for monitoring"
        )
    if not telegram_admin_chat_id:
        raise ValueError(
            "TELEGRAM_ADMIN_CHAT_ID environment variable is required for monitoring"
        )

    return MonitoringConfig.model_validate(
        {
            **monitoring_section,
            "telegram_token": telegram_token,
            "anthropic_api_key": anthropic_api_key,
            "telegram_admin_chat_id": telegram_admin_chat_id,
        }
    )
