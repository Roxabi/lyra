"""HTTP proxy to headless observability engines (#1774).

Browser never talks to Loki/Langfuse directly — dashboard BFF queries server-side.
MUST NOT import factory.infrastructure.* (ADR-094).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

import httpx

from roxabi_contracts.dashboard import (
    DashboardOpsHealthResponse,
    DashboardOpsLogsResponse,
    OpsEngineHealth,
    OpsEngineId,
    OpsLogEntry,
    OpsLogPreset,
)

if TYPE_CHECKING:
    pass

_PROBE_TIMEOUT_S = 3.0
_LOG_QUERY_TIMEOUT_S = 10.0

_LOG_PRESETS: dict[OpsLogPreset, str] = {
    "hub-errors": (
        '{job="factory-journal", systemd_unit="factory-hub.service"} |= "ERROR"'
    ),
    "operator-events": '{job="factory-operator"} | json | event=~"converge_.*"',
    "deploy-failures": (
        '{job="factory-journal", syslog_id="factory-deploy-failure"}'
    ),
    "container-journal": (
        '{job="factory-journal", systemd_unit="factory-hub.service"}'
    ),
}


def _systemd_unit_for_container(container: str) -> str:
    name = container.strip()
    if not name:
        raise ValueError("container name required")
    if name.endswith(".service"):
        return name
    return f"{name}.service"


def build_ops_log_query(
    preset: OpsLogPreset,
    *,
    container: str | None = None,
) -> str:
    if preset == "container-journal":
        if not container:
            raise ValueError(
                "container query param required for container-journal preset",
            )
        unit = _systemd_unit_for_container(container)
        return f'{{job="factory-journal", systemd_unit="{unit}"}}'
    if container:
        unit = _systemd_unit_for_container(container)
        return f'{{job="factory-journal", systemd_unit="{unit}"}}'
    return _LOG_PRESETS[preset]

_ENGINE_LABELS: dict[str, str] = {
    "loki": "Loki",
    "langfuse": "Langfuse",
    "otel-collector": "OTel Collector",
}


def _env_url(name: str, default: str) -> str:
    return os.environ.get(name, default).strip() or default


def loki_url() -> str:
    return _env_url("FACTORY_LOKI_URL", "http://127.0.0.1:3100")


def langfuse_url() -> str:
    return _env_url("FACTORY_LANGFUSE_URL", "http://127.0.0.1:3000")


def otel_health_url() -> str:
    return _env_url("FACTORY_OTEL_HEALTH_URL", "http://127.0.0.1:13133")


async def _probe(
    client: httpx.AsyncClient,
    *,
    engine: OpsEngineId,
    url: str,
    path: str,
    ok_status: set[int] | None = None,
) -> OpsEngineHealth:
    label = _ENGINE_LABELS.get(engine, engine)
    target = f"{url.rstrip('/')}{path}"
    try:
        res = await client.get(target)
        allowed = ok_status or {200}
        reachable = res.status_code in allowed
        detail = "ok" if reachable else f"HTTP {res.status_code}"
        return OpsEngineHealth(
            engine=engine,
            label=label,
            reachable=reachable,
            detail=detail,
        )
    except httpx.HTTPError as exc:
        return OpsEngineHealth(
            engine=engine,
            label=label,
            reachable=False,
            detail=str(exc),
        )


async def fetch_ops_health() -> DashboardOpsHealthResponse:
    async with httpx.AsyncClient(timeout=_PROBE_TIMEOUT_S) as client:
        engines = [
            await _probe(client, engine="loki", url=loki_url(), path="/ready"),
            await _probe(
                client,
                engine="langfuse",
                url=langfuse_url(),
                path="/api/public/health",
            ),
            await _probe(
                client,
                engine="otel-collector",
                url=otel_health_url(),
                path="/",
            ),
        ]
    return DashboardOpsHealthResponse(engines=engines)


def _parse_loki_streams(payload: dict, *, limit: int) -> list[OpsLogEntry]:
    entries: list[OpsLogEntry] = []
    result = payload.get("data", {}).get("result", [])
    if not isinstance(result, list):
        return entries
    for stream_block in result:
        if not isinstance(stream_block, dict):
            continue
        labels = stream_block.get("stream", {})
        if not isinstance(labels, dict):
            labels = {}
        values = stream_block.get("values", [])
        if not isinstance(values, list):
            continue
        for pair in values:
            if not isinstance(pair, list) or len(pair) < 2:
                continue
            ns_raw, line = pair[0], pair[1]
            try:
                ns = int(ns_raw)
                ts = datetime.fromtimestamp(ns / 1_000_000_000, tz=UTC).isoformat()
            except (TypeError, ValueError):
                ts = str(ns_raw)
            entries.append(
                OpsLogEntry(
                    timestamp=ts,
                    line=str(line),
                    labels={str(k): str(v) for k, v in labels.items()},
                )
            )
    entries.sort(key=lambda e: e.timestamp, reverse=True)
    return entries[:limit]


async def fetch_ops_logs(
    preset: OpsLogPreset,
    *,
    container: str | None = None,
    limit: int = 50,
    since_hours: int = 24,
) -> DashboardOpsLogsResponse:
    query = build_ops_log_query(preset, container=container)
    end = datetime.now(tz=UTC)
    start = end - timedelta(hours=since_hours)
    params = {
        "query": query,
        "limit": str(limit),
        "start": str(int(start.timestamp() * 1_000_000_000)),
        "end": str(int(end.timestamp() * 1_000_000_000)),
        "direction": "backward",
    }
    target = f"{loki_url().rstrip('/')}/loki/api/v1/query_range"
    try:
        async with httpx.AsyncClient(timeout=_LOG_QUERY_TIMEOUT_S) as client:
            res = await client.get(target, params=params)
            if res.status_code != 200:
                return DashboardOpsLogsResponse(
                    preset=preset,
                    query=query,
                    engine_reachable=False,
                    entries=[],
                )
            payload = res.json()
            if not isinstance(payload, dict):
                return DashboardOpsLogsResponse(
                    preset=preset,
                    query=query,
                    engine_reachable=True,
                    entries=[],
                )
            entries = _parse_loki_streams(payload, limit=limit)
            return DashboardOpsLogsResponse(
                preset=preset,
                query=query,
                engine_reachable=True,
                entries=entries,
            )
    except httpx.HTTPError:
        return DashboardOpsLogsResponse(
            preset=preset,
            query=query,
            engine_reachable=False,
            entries=[],
        )