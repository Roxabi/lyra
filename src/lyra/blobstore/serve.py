"""FastAPI app factory for the blobstore HTTP service."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse

from lyra.blobstore.auth import BearerAuthMiddleware

_METRICS_BODY = (
    "# HELP blobstore_up Whether the blobstore service is up\n"
    "# TYPE blobstore_up gauge\n"
    "blobstore_up 1\n"
)


def build_app(token: str) -> FastAPI:
    """Return a FastAPI app wired with auth middleware and V1 routes."""
    app = FastAPI(title="lyra-blobstore")
    app.add_middleware(BearerAuthMiddleware, token=token)

    @app.get("/healthz")
    async def healthz() -> dict:
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:
        return PlainTextResponse(
            _METRICS_BODY,
            media_type="text/plain; version=0.0.4",
        )

    @app.get("/blobs/{store_key}")
    async def get_blob(store_key: str) -> dict:
        # Placeholder — real handler wired in T7
        return {"placeholder": True}

    return app
