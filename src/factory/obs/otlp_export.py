"""Shared OTLP gRPC exporter wiring for factory services (#2069)."""

from __future__ import annotations

import os
from pathlib import Path

_DEFAULT_TOKEN_PATH = Path("/run/secrets/factory_otel_token")


def otlp_grpc_headers() -> dict[str, str] | None:
    """Bearer headers for factory-otel ingest when token path or env is set."""
    raw = os.environ.get("OTEL_EXPORTER_OTLP_HEADERS", "").strip()
    if raw:
        headers: dict[str, str] = {}
        for part in raw.split(","):
            if "=" not in part:
                continue
            key, value = part.split("=", 1)
            key = key.strip()
            if key:
                headers[key] = value.strip()
        if headers:
            return headers

    path = os.environ.get("FACTORY_OTEL_TOKEN_PATH", str(_DEFAULT_TOKEN_PATH)).strip()
    if path and Path(path).is_file():
        token = Path(path).read_text().strip()
        if token:
            return {"authorization": f"Bearer {token}"}
    return None


def otlp_grpc_endpoint() -> str:
    return os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()