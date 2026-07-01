"""Canonical paths for the otel-raw store."""

from __future__ import annotations

import os
from pathlib import Path

from factory.paths import factory_data_dir


def factory_otel_state_dir() -> Path:
    """JSONL archive directory (high-churn host-local state)."""
    default = Path.home() / ".local/state/factory/otel"
    return Path(os.environ.get("FACTORY_OTEL_STATE_DIR", default))


def factory_otel_jsonl_path() -> Path:
    return Path(
        os.environ.get(
            "FACTORY_OTEL_JSONL_PATH",
            factory_otel_state_dir() / "spans.jsonl",
        )
    )


def factory_otel_db_path() -> Path:
    return Path(
        os.environ.get(
            "FACTORY_OTEL_RAW_DB",
            factory_data_dir() / "otel-raw.db",
        )
    )