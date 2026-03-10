"""Monitoring configuration: thresholds from TOML, secrets from env vars."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass


@dataclass
class MonitoringConfig:
    """Configuration for the monitoring system.

    Thresholds come from [monitoring] section in lyra.toml.
    Secrets come from environment variables.
    """

    # Thresholds (from TOML)
    check_interval_minutes: int = 5
    health_endpoint_timeout_s: int = 5
    queue_depth_threshold: int = 80
    idle_threshold_hours: int = 6
    quiet_start: str = "00:00"
    quiet_end: str = "08:00"
    idle_check_enabled: bool = False
    min_disk_free_gb: int = 1
    health_endpoint_url: str = "http://localhost:8443/health"
    diagnostic_model: str = "claude-haiku-4-5-20251001"
    disk_check_path: str = "/"
    service_name: str = "lyra"

    # Secrets (from env vars)
    telegram_token: str = ""
    anthropic_api_key: str = ""
    telegram_admin_chat_id: str = ""


def load_monitoring_config(config_path: str | None = None) -> MonitoringConfig:
    """Load monitoring config from TOML thresholds + env var secrets.

    Config path resolution: config_path arg → $LYRA_CONFIG → lyra.toml in cwd.
    Missing config file → all defaults for thresholds.
    Missing required env vars → ValueError.
    """
    # Load TOML thresholds
    path = config_path or os.environ.get("LYRA_CONFIG", "lyra.toml")
    raw: dict = {}
    try:
        with open(path, "rb") as f:
            raw = tomllib.load(f)
    except FileNotFoundError:
        pass

    monitoring_section = raw.get("monitoring", {})

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

    return MonitoringConfig(
        check_interval_minutes=monitoring_section.get("check_interval_minutes", 5),
        health_endpoint_timeout_s=monitoring_section.get(
            "health_endpoint_timeout_s", 5
        ),
        queue_depth_threshold=monitoring_section.get("queue_depth_threshold", 80),
        idle_threshold_hours=monitoring_section.get("idle_threshold_hours", 6),
        quiet_start=monitoring_section.get("quiet_start", "00:00"),
        quiet_end=monitoring_section.get("quiet_end", "08:00"),
        idle_check_enabled=monitoring_section.get("idle_check_enabled", False),
        min_disk_free_gb=monitoring_section.get("min_disk_free_gb", 1),
        health_endpoint_url=monitoring_section.get(
            "health_endpoint_url", "http://localhost:8443/health"
        ),
        diagnostic_model=monitoring_section.get(
            "diagnostic_model", "claude-haiku-4-5-20251001"
        ),
        disk_check_path=monitoring_section.get("disk_check_path", "/"),
        service_name=monitoring_section.get("service_name", "lyra"),
        telegram_token=telegram_token,
        anthropic_api_key=anthropic_api_key,
        telegram_admin_chat_id=telegram_admin_chat_id,
    )
