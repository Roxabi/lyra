"""Monitoring configuration: thresholds from TOML, secrets from env vars."""

from __future__ import annotations

import os
import re
import tomllib
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from factory.paths import factory_data_dir

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
    service_names: list[str] = Field(
        default=["factory-hub", "factory-telegram", "factory-discord"]
    )
    health_secret: str = ""
    nats_container_name: str = "factory-nats"
    hub_container_name: str = "factory-hub"
    nats_log_check_minutes: int = 30
    stream_gen_timeout_minutes: int = 30
    stream_gen_timeout_threshold: int = 3
    nats_monitor_url: str = "http://127.0.0.1:8222"
    nats_monitor_state_file: str = Field(
        default_factory=lambda: str(factory_data_dir() / "nats-monitor-state.json")
    )
    log_level: str = "info"
    blobstore_disk_path: str = "/data/lyra/blobs"
    blobstore_disk_warning_pct: int = 60
    blobstore_disk_critical_pct: int = 70
    blobstore_inode_warning_pct: int = 60
    blobstore_inode_critical_pct: int = 70
    # Outbound-audio JetStream checks (#1482 T11)
    # consumer lag: alert when num_pending exceeds this value
    audio_lag_pending_threshold: int = 50
    # consumer lag: alert when oldest unacked message is older than this (seconds)
    # default 72000 s = 20 h — approaching the 24 h MaxAge silent-loss bound (D4)
    audio_lag_age_warn_s: int = 72000
    # stream fullness: alert when stream bytes exceed this % of max_bytes
    audio_stream_usage_warn_pct: int = 80

    # Secrets (from env vars)
    telegram_token: str = Field(default="", repr=False)
    telegram_admin_chat_id: str = Field(default="", repr=False)

    @field_validator("quiet_start", "quiet_end")
    @classmethod
    def _validate_hhmm(cls, v: str) -> str:
        if not _HHMM_RE.match(v):
            raise ValueError(f"must be HH:MM format, got {v!r}")
        return v

    @field_validator("service_names")
    @classmethod
    def _validate_service_names(cls, v: list[str]) -> list[str]:
        for name in v:
            if not _SERVICE_NAME_RE.match(name):
                raise ValueError(
                    f"service_names entries must match [a-zA-Z0-9_@.-]+, got {name!r}"
                )
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

    @field_validator(
        "blobstore_disk_warning_pct",
        "blobstore_disk_critical_pct",
        "blobstore_inode_warning_pct",
        "blobstore_inode_critical_pct",
    )
    @classmethod
    def _validate_pct(cls, v: int) -> int:
        if not 0 <= v <= 100:
            raise ValueError("must be between 0 and 100")
        return v

    @model_validator(mode="after")
    def _validate_threshold_order(self) -> MonitoringConfig:
        if self.blobstore_disk_warning_pct >= self.blobstore_disk_critical_pct:
            raise ValueError(
                "blobstore_disk_warning_pct must be less than "
                "blobstore_disk_critical_pct"
            )
        if self.blobstore_inode_warning_pct >= self.blobstore_inode_critical_pct:
            raise ValueError(
                "blobstore_inode_warning_pct must be less than "
                "blobstore_inode_critical_pct"
            )
        return self


def load_monitoring_config(config_path: str | None = None) -> MonitoringConfig:
    """Load monitoring config from TOML thresholds + env var secrets.

    Config path resolution: config_path arg → $FACTORY_CONFIG → lyra.toml in cwd.
    Missing config file → all defaults for thresholds.
    Missing required env vars → ValueError.
    """
    # Load TOML thresholds
    path = config_path or os.environ.get("FACTORY_CONFIG", "lyra.toml")
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
    telegram_admin_chat_id = os.environ.get("TELEGRAM_ADMIN_CHAT_ID", "")
    health_secret = os.environ.get("FACTORY_HEALTH_SECRET", "")

    if not telegram_token:
        raise ValueError(
            "TELEGRAM_TOKEN environment variable is required for monitoring"
        )
    if not telegram_admin_chat_id:
        raise ValueError(
            "TELEGRAM_ADMIN_CHAT_ID environment variable is required for monitoring"
        )

    return MonitoringConfig.model_validate(
        {
            **monitoring_section,
            "telegram_token": telegram_token,
            "telegram_admin_chat_id": telegram_admin_chat_id,
            "health_secret": health_secret,
        }
    )
