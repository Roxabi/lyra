"""FastAPI app factory for the blobstore HTTP service."""

from __future__ import annotations

import pathlib
from contextlib import asynccontextmanager
from typing import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from lyra.blobstore._handlers import handle_delete, handle_get, handle_head, handle_put
from lyra.blobstore.auth import BearerAuthMiddleware
from roxabi_blobs import FsBlobStore

_METRICS_BODY = (
    "# HELP blobstore_up Whether the blobstore service is up\n"
    "# TYPE blobstore_up gauge\n"
    "blobstore_up 1\n"
)


def build_app(
    *,
    token_path: pathlib.Path | None = None,
    blob_root: pathlib.Path,
    token: str | None = None,
) -> FastAPI:
    """Return a FastAPI app wired with auth middleware and V2 routes.

    Token read ONCE from `token_path` at startup (SC-Code-5).  Pass `token`
    as a plain string for test-only usage (avoids a temp file in fixtures).
    `FsBlobStore` is opened for the lifetime of the process via the ASGI lifespan.
    """
    if token is None:
        if token_path is None:
            raise ValueError("Either token_path or token must be provided.")
        token = token_path.read_text().strip()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        async with FsBlobStore(root=blob_root) as store:
            app.state.store = store
            yield

    app = FastAPI(title="lyra-blobstore", lifespan=lifespan)
    app.add_middleware(BearerAuthMiddleware, token=token)

    @app.get("/healthz")
    async def healthz() -> dict:  # N5 — no auth (allowlist in BearerAuthMiddleware)
        return {"status": "ok"}

    @app.get("/metrics")
    async def metrics() -> PlainTextResponse:  # N6 — no auth
        return PlainTextResponse(
            _METRICS_BODY,
            media_type="text/plain; version=0.0.4",
        )

    @app.put("/blobs")
    async def put_blob(request: Request) -> Response:  # N1
        return await handle_put(request)

    @app.get("/blobs/{store_key:path}")
    async def get_blob(store_key: str, request: Request) -> Response:  # N2
        return await handle_get(store_key, request)

    @app.head("/blobs/{store_key:path}")
    async def head_blob(store_key: str, request: Request) -> Response:  # N3
        return await handle_head(store_key, request)

    @app.delete("/blobs/{key:path}")
    async def delete_blob(key: str, request: Request) -> Response:  # N4
        return await handle_delete(key, request)

    return app
