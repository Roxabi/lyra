"""Bootstrap health app — FastAPI health endpoint for hub monitoring."""

from __future__ import annotations

import hmac
import logging
import os
import time
from functools import cached_property
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException

from lyra.core.hub import Hub

log = logging.getLogger(__name__)


class Secrets:
    def __init__(self, vault_dir: Path | None = None) -> None:
        self._vault_dir = vault_dir or Path(
            os.environ.get("LYRA_VAULT_DIR", str(Path.home() / ".lyra"))
        ).resolve()

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
    except Exception as exc:
        log.debug("_probe_nats: unexpected exception from nc.is_connected: %s", exc)
        return "unreachable"



def create_health_app(  # noqa: C901 — optional sections (nats/reaper/circuits)
    hub: Hub, nc: Any | None = None, secrets: Secrets | None = None
) -> FastAPI:
    """Create a root FastAPI app with /health endpoint for hub monitoring.

    This is the top-level HTTP app — adapter sub-apps can be mounted on it.
    The /health endpoint exposes hub-level health without requiring adapter auth.

    When *nc* is provided (three-process NATS mode), ``/health/detail``
    surfaces NATS reachability under the ``nats`` key and an overall
    ``status`` of ``ok``/``degraded``. When ``NATS_URL`` is unset both
    fields are omitted.
    """
    _secrets = secrets or Secrets()
    app = FastAPI(title="Lyra Hub")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

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

        uptime_s = time.monotonic() - hub._start_time

        last_message_age_s: float | None = None
        if hub._last_processed_at is not None:
            last_message_age_s = time.monotonic() - hub._last_processed_at

        circuits: dict[str, dict[str, object]] = {}
        if hub.circuit_registry is not None:
            all_status = hub.circuit_registry.get_all_status()
            circuits = {
                name: {
                    "state": s.state.value,
                    "retry_after": s.retry_after,
                }
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

    return app
