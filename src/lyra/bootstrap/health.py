"""Bootstrap health app — FastAPI health endpoint for hub monitoring."""

from __future__ import annotations

import hmac
import logging
import time
from pathlib import Path

from fastapi import FastAPI, Header, HTTPException

from lyra.core.hub import Hub

log = logging.getLogger(__name__)


def _read_secret(name: str) -> str:
    """Read a secret from ~/.lyra/secrets/{name}. Returns '' if missing."""
    path = Path.home() / ".lyra" / "secrets" / name
    try:
        return path.read_text().strip()
    except FileNotFoundError:
        return ""
    except OSError as exc:
        log.warning("Could not read secret %r: %s", name, exc)
        return ""


def create_health_app(hub: Hub) -> FastAPI:
    """Create a root FastAPI app with /health endpoint for hub monitoring.

    This is the top-level HTTP app — adapter sub-apps can be mounted on it.
    The /health endpoint exposes hub-level health without requiring adapter auth.
    """
    app = FastAPI(title="Lyra Hub")

    @app.get("/health")
    async def health() -> dict:
        return {"ok": True}

    @app.get("/health/detail")
    async def health_detail(authorization: str = Header(default="")) -> dict:
        health_secret = _read_secret("health_secret")
        expected = f"Bearer {health_secret}"
        if not health_secret or not hmac.compare_digest(authorization, expected):
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

        result = {
            "ok": True,
            "queue_size": hub.inbound_bus.staging_qsize(),
            "queues": {"inbound": inbound, "outbound": outbound},
            "last_message_age_s": last_message_age_s,
            "uptime_s": round(uptime_s, 1),
            "circuits": circuits,
            "adapters": len(hub.adapter_registry),
        }

        # Reaper fields only present when a CLI pool is configured
        if hub.cli_pool is not None:
            result["reaper_alive"] = (
                hub.cli_pool._reaper_task is not None
                and not hub.cli_pool._reaper_task.done()
            )
            result["reaper_last_sweep_age"] = (
                round(
                    time.monotonic() - hub.cli_pool._last_sweep_at,
                    1,
                )
                if hub.cli_pool._last_sweep_at is not None
                else None
            )
            result["dead_backend_hits"] = hub.cli_pool.dead_backend_hits

        return result

    @app.get("/config")
    async def config_endpoint(
        authorization: str = Header(default=""),
        agent: str = "lyra_default",
    ) -> dict:
        config_secret = _read_secret("config_secret")
        if not config_secret or not hmac.compare_digest(
            authorization, f"Bearer {config_secret}"
        ):
            raise HTTPException(status_code=401, detail="unauthorized")
        from lyra.agents.anthropic_agent import AnthropicAgent

        agent_obj = hub.agent_registry.get(agent)
        if not isinstance(agent_obj, AnthropicAgent):
            raise HTTPException(
                status_code=404,
                detail="runtime config not available for this agent backend",
            )
        rc = agent_obj.runtime_config
        return {
            "style": rc.style,
            "language": rc.language,
            "temperature": rc.temperature,
            "model": rc.model,
            "max_steps": rc.max_steps,
            "extra_instructions": rc.extra_instructions,
        }

    return app
