"""FastAPI app factory for the blobstore HTTP service."""

from __future__ import annotations

import logging
import os
import pathlib
import shutil
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import PlainTextResponse, Response

from lyra.blobstore._handlers import handle_delete, handle_get, handle_head, handle_put
from lyra.blobstore.audit_sink import BlobAuditSink
from lyra.blobstore.auth import BearerAuthMiddleware
from roxabi_blobs import FsBlobStore

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

_log = logging.getLogger(__name__)


async def _disk_used_pct(blob_root: pathlib.Path) -> float | None:
    """Return disk usage percentage for the blob root mount, or None on failure."""
    try:
        usage = shutil.disk_usage(blob_root)
        return round(usage.used / usage.total * 100, 1)
    except Exception:  # noqa: BLE001
        _log.warning("BLOBSTORE: disk_usage failed for %s", blob_root)
        return None


async def _blob_count(app: FastAPI) -> int | None:
    """Return total row count from the blobs table, or None on failure."""
    try:
        store: FsBlobStore = app.state.store
        conn = store._conn  # noqa: SLF001
        if conn is None:
            return None
        cursor = await conn.execute("SELECT COUNT(*) FROM blobs")
        row = await cursor.fetchone()
        await cursor.close()
        return int(row[0]) if row is not None else None
    except Exception:  # noqa: BLE001
        _log.warning("BLOBSTORE: blob_count query failed")
        return None


async def _provision_nats(app: FastAPI, nc: NATS) -> None:
    """Wire JetStream audit sink and KV readiness announce."""
    from nats.js.errors import BucketNotFoundError

    try:
        js = nc.jetstream()
        sink = BlobAuditSink()
        sink._js = js  # noqa: SLF001
        app.state.audit_sink = sink
    except Exception:  # noqa: BLE001
        _log.warning("BLOBSTORE: JetStream unavailable — audit sink degraded")
        app.state.audit_sink = BlobAuditSink()
        return

    try:
        try:
            kv = await js.key_value("lyra-state")
        except BucketNotFoundError:
            from nats.js.api import KeyValueConfig, StorageType

            kv = await js.create_key_value(
                KeyValueConfig(bucket="lyra-state", storage=StorageType.FILE)
            )
        await kv.put("blobstore.ready", b"true")
        _log.info("Blobstore KV ready announced")
    except Exception:  # noqa: BLE001
        _log.warning("BLOBSTORE: KV readiness announce failed", exc_info=True)

    app.state.nats_provisioned = True


async def _connect_nats() -> NATS | None:
    """Attempt production NATS connect; return None on failure (degraded mode)."""
    nats_url = os.environ.get("NATS_URL")
    if not nats_url:
        _log.warning("BLOBSTORE: NATS_URL not set — running in degraded mode")
        return None
    try:
        from roxabi_nats import nats_connect

        return await nats_connect(nats_url, identity_name="blobstore")
    except Exception:  # noqa: BLE001
        _log.warning("BLOBSTORE: NATS connect failed — running in degraded mode")
        return None


async def _maybe_provision(app: FastAPI) -> None:
    """Lazy-init NATS when lifespan was not triggered (e.g. ASGITransport tests)."""
    if app.state.nats_provisioned:
        return
    nc: NATS | None = getattr(app.state, "nats_client", None)
    if nc is not None:
        await _provision_nats(app, nc)


def _make_lifespan(blob_root: pathlib.Path, injected_nats: NATS | None):  # type: ignore[return]
    """Return an asynccontextmanager lifespan for build_app."""

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        nc = injected_nats
        _own_nc = nc is None
        if nc is None:
            nc = await _connect_nats()

        async with FsBlobStore(root=blob_root) as store:
            app.state.store = store
            app.state.audit_sink = BlobAuditSink()
            app.state.nats_provisioned = False
            app.state.nats_client = nc
            if nc is not None:
                await _provision_nats(app, nc)
            yield

        if nc is not None and _own_nc:
            await nc.close()

    return lifespan


def build_app(
    *,
    token_path: pathlib.Path | None = None,
    blob_root: pathlib.Path,
    token: str | None = None,
    nats: NATS | None = None,
) -> FastAPI:
    """Return a FastAPI app wired with auth middleware and V2 routes.

    Token read ONCE from `token_path` at startup (SC-Code-5).  Pass `token`
    as a plain string for test-only usage (avoids a temp file in fixtures).
    `FsBlobStore` is opened for the lifetime of the process via the ASGI lifespan.
    Pass `nats` to inject a pre-connected NATS client (tests + production inject path).
    On NATS failure, proceeds in degraded mode (SC-Code-S4: ¬abort startup).
    """
    if token is None:
        if token_path is None:
            raise ValueError("Either token_path or token must be provided.")
        token = token_path.read_text().strip()

    app = FastAPI(title="lyra-blobstore", lifespan=_make_lifespan(blob_root, nats))
    app.add_middleware(BearerAuthMiddleware, token=token)

    # Stash for lazy-init (when lifespan doesn't run, e.g. tests via ASGITransport)
    app.state.nats_client = nats
    app.state.nats_provisioned = False
    app.state.audit_sink = BlobAuditSink()

    _register_routes(app, blob_root=blob_root)
    return app


def _register_routes(app: FastAPI, *, blob_root: pathlib.Path) -> None:
    """Attach all HTTP routes to the app."""

    @app.get("/healthz")
    async def healthz(request: Request) -> dict:  # N5 — no auth
        await _maybe_provision(request.app)
        used_pct = await _disk_used_pct(blob_root)
        count = await _blob_count(request.app)
        return {"status": "ok", "disk_used_pct": used_pct, "blob_count": count}

    @app.get("/metrics")
    async def metrics(request: Request) -> PlainTextResponse:  # N6 — no auth
        used_pct = await _disk_used_pct(blob_root)
        count = await _blob_count(request.app)
        used_val = used_pct if used_pct is not None else 0.0
        count_val = count if count is not None else 0
        body = (
            "# HELP blobstore_up Whether the blobstore service is up\n"
            "# TYPE blobstore_up gauge\n"
            "blobstore_up 1\n"
            "# HELP blobstore_disk_used_pct Disk usage of blob root mount (0..100)\n"
            "# TYPE blobstore_disk_used_pct gauge\n"
            f"blobstore_disk_used_pct {used_val}\n"
            "# HELP blobstore_blob_count Total rows in the blobs table\n"
            "# TYPE blobstore_blob_count gauge\n"
            f"blobstore_blob_count {count_val}\n"
        )
        return PlainTextResponse(body, media_type="text/plain; version=0.0.4")

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
