"""HTTP client for factory-otel span queries (ADR-097)."""

from __future__ import annotations

import os
from pathlib import Path

import httpx

_DEFAULT_URL = "http://factory-otel:8450"
_DEFAULT_TOKEN_PATH = Path("/run/secrets/factory_otel_token")


def otel_base_url() -> str:
    return os.environ.get("FACTORY_OTEL_URL", _DEFAULT_URL).strip().rstrip("/")


def _otel_token() -> str:
    path = os.environ.get("FACTORY_OTEL_TOKEN_PATH", str(_DEFAULT_TOKEN_PATH))
    return Path(path).read_text().strip()


async def fetch_spans(
    *,
    pool_id: str | None = None,
    job_id: str | None = None,
    component: str | None = None,
    page: int = 1,
    page_size: int = 50,
) -> dict:
    params: dict[str, str | int] = {"page": page, "page_size": page_size}
    if pool_id:
        params["pool_id"] = pool_id
    if job_id:
        params["job_id"] = job_id
    if component:
        params["component"] = component
    async with httpx.AsyncClient(timeout=10.0) as client:
        res = await client.get(
            f"{otel_base_url()}/api/spans",
            params=params,
            headers={"Authorization": f"Bearer {_otel_token()}"},
        )
        res.raise_for_status()
        return res.json()