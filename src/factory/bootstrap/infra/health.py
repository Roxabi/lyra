"""Bootstrap health app — FastAPI health endpoint for hub monitoring."""

from __future__ import annotations

import asyncio
import hmac
import logging
import os
import time
from functools import cached_property
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse, PlainTextResponse

from factory.core.hub import Hub
from factory.paths import factory_data_dir

log = logging.getLogger(__name__)


def create_health_server(  # noqa: PLR0913 — health surface
    hub: Hub,
    nc: Any | None = None,
    *,
    port_env: str = "FACTORY_HEALTH_PORT",
    host_env: str = "FACTORY_HEALTH_HOST",
    default_port: int = 8443,
    default_host: str = "127.0.0.1",
) -> tuple[Any, int]:
    """Build a Uvicorn server for the hub health endpoint.

    Returns the Uvicorn server instance and the resolved port.
    """
    import uvicorn

    health_port = int(os.environ.get(port_env, str(default_port)))
    health_host = os.environ.get(host_env, default_host)
    health_app = create_health_app(hub, nc=nc)
    health_config = uvicorn.Config(
        health_app, host=health_host, port=health_port, log_level="warning"
    )
    return uvicorn.Server(health_config), health_port


class Secrets:
    def __init__(self, vault_dir: Path | None = None) -> None:
        self._vault_dir = vault_dir or factory_data_dir().resolve()

    def _read(self, name: str) -> str:
        path = self._vault_dir / "secrets" / name
        try:
            return path.read_text().strip()
        except FileNotFoundError:
            return ""
        except OSError as exc:
            log.warning("Could not read secret %r: %s", name, exc)
            return ""

    @cached_property
    def health_secret(self) -> str:
        return self._read("health_secret")


def _probe_nats(nc: Any | None) -> str | None:
    """Return NATS status string when NATS is configured, else ``None``.

    NATS is considered configured when ``NATS_URL`` is present in the
    environment (mirrors the bootstrap startup check). When configured:
    - ``nc`` connected  → ``"ok"``
    - ``nc`` disconnected / absent → ``"unreachable"``

    When ``NATS_URL`` is not set the ``nats`` field is omitted from health
    responses entirely (no log noise, no degradation). See #449.
    """
    if not os.environ.get("NATS_URL"):
        return None
    if nc is None:
        return "unreachable"
    try:
        return "ok" if bool(nc.is_connected) else "unreachable"
    except AttributeError as exc:
        log.debug("_probe_nats: unexpected exception from nc.is_connected: %s", exc)
        return "unreachable"


# Bounded readiness probe — a live server reports its *current* state, it does
# not block waiting for one (unlike roxabi_nats.readiness.wait_for_hub, which
# is a boot-time barrier with retry + KV-watch fallback). Keeps the HTTP
# handler fast even under a wedged NATS/JetStream backend (#2202).
_READY_TIMEOUT_S = 5.0


async def _probe_nats_ready(nc: Any | None) -> tuple[bool, str | None]:
    """Round-trip through NATS/JetStream KV to prove the message plane is live.

    Fetches ``hub.ready`` from the ``factory-state`` KV bucket — the same key
    ``announce_hub_ready()`` writes on hub startup and ``wait_for_hub()``
    (``roxabi_nats.readiness``) reads at adapter boot — but as a single
    bounded attempt rather than a retry/watch loop.

    This is a strictly stronger signal than ``_probe_nats``'s ``nc.is_connected``
    check: that TCP flag stayed ``True`` throughout the 2026-04-27 incident
    (hub alive, adapters unreachable for 3h15m) because it only reflects
    socket state, not JetStream/ACL health (#2202).

    Returns ``(ready, reason)`` — ``reason`` is ``None`` when ready, else a
    short machine-readable explanation for the failure.
    """
    if nc is None:
        return False, "nats client not configured"

    import nats.errors
    from nats.js.errors import BucketNotFoundError, KeyNotFoundError

    try:
        async with asyncio.timeout(_READY_TIMEOUT_S):
            js = nc.jetstream()
            kv = await js.key_value("factory-state")
            entry = await kv.get("hub.ready")
    except TimeoutError:
        return False, "nats round-trip timed out"
    except BucketNotFoundError:
        return False, "factory-state bucket not provisioned"
    except KeyNotFoundError:
        return False, "hub.ready key not found"
    except nats.errors.Error as exc:
        return False, f"nats error: {exc}"
    except Exception:
        log.exception("_probe_nats_ready: unexpected error during KV round-trip")
        return False, "unexpected error"

    if entry.value != b"true":
        return False, "hub.ready value unexpected"
    return True, None


def _collect_hub_detail(  # noqa: C901 — optional sections (nats/reaper/circuits)
    hub: Hub, nc: Any | None
) -> dict[str, Any]:
    """Assemble the hub health/metrics snapshot.

    Shared verbatim by ``/health/detail`` (JSON) and ``/metrics`` (Prometheus),
    so both surfaces read the same single collection path (no drift).
    """
    uptime_s = time.monotonic() - hub._start_time

    last_message_age_s: float | None = None
    if hub._last_processed_at is not None:
        last_message_age_s = time.monotonic() - hub._last_processed_at

    circuits: dict[str, dict[str, object]] = {}
    if hub.circuit_registry is not None:
        all_status = hub.circuit_registry.get_all_status()
        circuits = {
            name: {"state": s.state.value, "retry_after": s.retry_after}
            for name, s in all_status.items()
        }

    inbound: dict[str, int] = {
        p.value: hub.inbound_bus.qsize(p)
        for p in hub.inbound_bus.registered_platforms()
    }
    outbound: dict[str, int] = {
        platform.value: dispatcher.qsize()
        for (platform, _bot_id), dispatcher in hub.outbound_dispatchers.items()
    }

    result: dict[str, Any] = {
        "ok": True,
        "queue_size": hub.inbound_bus.staging_qsize(),
        "queues": {"inbound": inbound, "outbound": outbound},
        "last_message_age_s": last_message_age_s,
        "uptime_s": round(uptime_s, 1),
        "circuits": circuits,
        "adapters": len(hub.adapter_registry),
        "buses": hub.inbound_bus.subscription_count,
    }

    nats_status = _probe_nats(nc)
    if nats_status is not None:
        result["nats"] = nats_status
        result["status"] = "degraded" if nats_status == "unreachable" else "ok"

    # Reaper fields only present when a CLI pool is configured
    if hub.cli_pool is not None:
        reaper_status = hub.cli_pool.get_reaper_status()
        result["reaper_alive"] = reaper_status["alive"]
        result["reaper_last_sweep_age"] = reaper_status["last_sweep_age"]
    return result


_CIRCUIT_STATE_CODE = {"closed": 0, "half_open": 1, "open": 2}


def _esc(value: str) -> str:
    """Escape a Prometheus label value (backslash, quote, newline)."""
    return value.replace("\\", "\\\\").replace('"', '\\"').replace("\n", "\\n")


def _gauge(name: str, help_text: str, samples: list[tuple[str, float]]) -> list[str]:
    """Render one gauge block (HELP/TYPE + samples); empty when no samples."""
    if not samples:
        return []
    out = [f"# HELP {name} {help_text}", f"# TYPE {name} gauge"]
    out.extend(f"{name}{labels} {value}" for labels, value in samples)
    return out


def _render_prometheus(detail: dict[str, Any]) -> str:
    """Format a ``_collect_hub_detail`` snapshot as Prometheus text (0.0.4)."""
    inbound = detail["queues"]["inbound"]
    outbound = detail["queues"]["outbound"]
    circuits = detail["circuits"]
    lines: list[str] = []
    lines += _gauge(
        "factory_hub_queue_size",
        "Inbound staging queue size.",
        [("", detail["queue_size"])],
    )
    lines += _gauge(
        "factory_uptime_seconds", "Hub uptime in seconds.", [("", detail["uptime_s"])]
    )
    lines += _gauge(
        "factory_adapters_total",
        "Registered inbound adapters.",
        [("", detail["adapters"])],
    )
    lines += _gauge(
        "factory_bus_subscriptions_total",
        "Active inbound bus subscriptions.",
        [("", detail["buses"])],
    )
    if detail.get("last_message_age_s") is not None:
        lines += _gauge(
            "factory_last_message_age_seconds",
            "Seconds since the hub last processed a message.",
            [("", detail["last_message_age_s"])],
        )
    lines += _gauge(
        "factory_inbound_queue_depth",
        "Inbound queue depth per platform.",
        [(f'{{platform="{_esc(p)}"}}', q) for p, q in inbound.items()],
    )
    lines += _gauge(
        "factory_outbound_queue_depth",
        "Outbound dispatcher queue depth per platform.",
        [(f'{{platform="{_esc(p)}"}}', q) for p, q in outbound.items()],
    )
    lines += _gauge(
        "factory_circuit_state",
        "Circuit-breaker state (0=closed, 1=half_open, 2=open, -1=unknown).",
        [
            (f'{{circuit="{_esc(n)}"}}', _CIRCUIT_STATE_CODE.get(str(c["state"]), -1))
            for n, c in circuits.items()
        ],
    )
    lines += _gauge(
        "factory_circuit_retry_after_seconds",
        "Circuit-breaker retry-after window in seconds.",
        [
            (f'{{circuit="{_esc(n)}"}}', c["retry_after"])
            for n, c in circuits.items()
            if c.get("retry_after") is not None
        ],
    )
    if "nats" in detail:
        lines += _gauge(
            "factory_nats_up",
            "NATS reachability (1=ok, 0=unreachable).",
            [("", 1 if detail["nats"] == "ok" else 0)],
        )
    if "reaper_alive" in detail:
        lines += _gauge(
            "factory_reaper_alive",
            "CLI-pool reaper liveness (1=alive, 0=dead).",
            [("", 1 if detail["reaper_alive"] else 0)],
        )
    if detail.get("reaper_last_sweep_age") is not None:
        lines += _gauge(
            "factory_reaper_last_sweep_age_seconds",
            "Seconds since the CLI-pool reaper last swept.",
            [("", detail["reaper_last_sweep_age"])],
        )
    return "\n".join(lines) + "\n"


def create_health_app(
    hub: Hub, nc: Any | None = None, secrets: Secrets | None = None
) -> FastAPI:
    """Create a root FastAPI app with /health endpoint for hub monitoring.

    This is the top-level HTTP app — adapter sub-apps can be mounted on it.
    The /health endpoint exposes hub-level health without requiring adapter auth.

    When *nc* is provided (three-process NATS mode), ``/health/detail``
    surfaces NATS reachability under the ``nats`` key and an overall
    ``status`` of ``ok``/``degraded``. When ``NATS_URL`` is unset both
    fields are omitted.

    ``/health/ready`` (#2202) is a separate, unauthenticated readiness probe:
    process liveness (``/health``) says nothing about whether the hub can
    actually reach NATS/JetStream, so it round-trips a KV read instead of
    trusting the passive ``nc.is_connected`` TCP flag. Deliberately **not**
    wired into the Quadlet ``HealthCmd``/auto-restart — see the probe's
    docstring and #2202 for the incident/ADR-065 rationale.
    """
    _secrets = secrets or Secrets()
    app = FastAPI(title="factory Hub")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/health/ready")
    async def health_ready() -> JSONResponse:
        ready, reason = await _probe_nats_ready(nc)
        return JSONResponse(
            {"ready": ready, "reason": reason}, status_code=200 if ready else 503
        )

    @app.get("/health/detail")
    async def health_detail(authorization: str = Header(default="")) -> dict:
        health_secret = _secrets.health_secret
        expected = f"Bearer {health_secret}"
        # Encode both sides to bytes: hmac.compare_digest requires matching
        # types; passing mixed str/bytes raises TypeError (becomes a 500)
        # rather than the intended 401. Fix guards against future type drift.
        if not health_secret or not hmac.compare_digest(
            authorization.encode("utf-8"), expected.encode("utf-8")
        ):
            raise HTTPException(status_code=401, detail="unauthorized")

        return _collect_hub_detail(hub, nc)

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        # Prometheus scrape surface (ADR-092 plane ③ pull). Unauthenticated like
        # /health — the server binds 127.0.0.1 by default and the gauges are
        # non-sensitive operational counters.
        detail = _collect_hub_detail(hub, nc)
        return PlainTextResponse(
            _render_prometheus(detail),
            media_type="text/plain; version=0.0.4; charset=utf-8",
        )

    return app
