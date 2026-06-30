"""FastAPI app factory for factory-ingress."""

from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from factory.infrastructure.stores.ingress.installation_store import (
    InstallationStore,
    default_db_path,
)
from factory.ingress.config import IngressConfig, load_config
from factory.ingress.connectors.registry import ConnectorRegistry, default_registry
from factory.ingress.publisher import EventPublisher
from factory.ingress.routes import build_router
from factory.ingress.secrets import PodmanSecretResolver
from roxabi_nats import nats_connect

log = logging.getLogger(__name__)


def create_app(
    config: IngressConfig | None = None,
    *,
    publisher: EventPublisher | None = None,
    installations: InstallationStore | None = None,
    registry: ConnectorRegistry | None = None,
) -> FastAPI:
    cfg = config or load_config()
    reg = registry or default_registry()
    secrets = PodmanSecretResolver(cfg)
    state: dict[str, Any] = {
        "publisher": publisher,
        "nc": None,
        "installations": installations,
    }

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        store = state["installations"]
        if store is None:
            store = InstallationStore(default_db_path())
            await store.connect()
            state["installations"] = store
            gh_id = os.environ.get("INGRESS_GITHUB_INSTALLATION_ID", "").strip()
            if gh_id and cfg.is_enabled("github"):
                await store.seed("github", gh_id, "default")
            elif cfg.is_enabled("github") and not gh_id:
                log.warning(
                    "github connector enabled but INGRESS_GITHUB_INSTALLATION_ID unset"
                )
        if state["publisher"] is None:
            nats_url = os.environ.get("NATS_URL", "nats://factory-nats:4222")
            nc = await nats_connect(nats_url, identity_name="ingress")
            from roxabi_obs import start_fleet_reporter

            state["fleet_reporter_task"] = await start_fleet_reporter(nc)
            state["publisher"] = EventPublisher(nc.jetstream())
            state["nc"] = nc
        enabled = [n for n in reg.names() if cfg.is_enabled(n)]
        log.info("factory-ingress ready connectors=%s", enabled)
        yield
        from roxabi_obs import cancel_fleet_reporter

        await cancel_fleet_reporter(state.get("fleet_reporter_task"))
        nc = state.get("nc")
        if nc is not None:
            await nc.close()
        inst: InstallationStore | None = state.get("installations")
        if inst is not None and installations is None:
            await inst.close()

    def _get_installations() -> InstallationStore:
        inst = state.get("installations")
        if inst is None:
            raise RuntimeError("installation store not ready")
        return inst

    app = FastAPI(
        title="factory-ingress",
        docs_url=None,
        redoc_url=None,
        lifespan=lifespan,
    )

    app.include_router(
        build_router(
            cfg,
            lambda: state.get("publisher"),
            registry=reg,
            get_installations=_get_installations,
            secrets=secrets,
        )
    )
    return app