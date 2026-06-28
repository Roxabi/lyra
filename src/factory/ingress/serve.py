"""FastAPI app factory for factory-ingress."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from factory.ingress.config import IngressConfig, load_config
from factory.ingress.publisher import EventPublisher
from factory.ingress.routes import build_router
from roxabi_nats import nats_connect

log = logging.getLogger(__name__)


def create_app(
    config: IngressConfig | None = None,
    *,
    publisher: EventPublisher | None = None,
) -> FastAPI:
    cfg = config or load_config()
    state: dict[str, Any] = {"publisher": publisher, "nc": None}

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        if state["publisher"] is None:
            import os

            nats_url = os.environ.get("NATS_URL", "nats://factory-nats:4222")
            nc = await nats_connect(nats_url, identity_name="ingress")
            state["publisher"] = EventPublisher(nc.jetstream())
            state["nc"] = nc
        log.info(
            "factory-ingress ready (github=%s cloudflare=%s)",
            cfg.github.enabled,
            cfg.cloudflare.enabled,
        )
        yield
        nc = state.get("nc")
        if nc is not None:
            await nc.close()

    app = FastAPI(
        title="factory-ingress",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )
    app.include_router(build_router(cfg, lambda: state.get("publisher")))
    return app