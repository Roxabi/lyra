"""HTTP handler implementations for the blobstore service (N1–N4).

Extracted from `serve.py` to satisfy the 300-line / complexity limits.
These are *not* a public API — import only from `serve.py`.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Literal, TypeVar
from uuid import uuid4

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from factory.blobstore._keys import resolve_delete_key, resolve_wire_key
from roxabi_blobs import FsBlobStore
from roxabi_blobs.errors import BlobNotFoundError, BlobWriteError
from roxabi_contracts.audit.blobs import BlobAuditEvent
from roxabi_contracts.envelope import CONTRACT_VERSION

_R = TypeVar("_R", bound=Response)

_log = logging.getLogger(__name__)

# Matches a bare decimal integer (blob_ref_id) — no leading zeros, no sign.
_INT_KEY_RE = re.compile(r"^\d+$")


# ---------------------------------------------------------------------------
# Audit helper
# ---------------------------------------------------------------------------


async def _emit_audit(  # noqa: PLR0913
    app: FastAPI,
    *,
    op: Literal["put", "get", "exists", "delete"],
    result: Literal[
        "ok", "not_found", "unauthorized", "write_failed", "internal_error"
    ],
    store_key: str | None = None,
    content_hash: str | None = None,
    size: int | None = None,
    source: str | None = None,
) -> None:
    """Best-effort audit; never raises — audit failure must not break a request."""
    sink = getattr(app.state, "audit_sink", None)
    if sink is None:
        return
    try:
        event = BlobAuditEvent(
            contract_version=CONTRACT_VERSION,
            trace_id=str(uuid4()),
            issued_at=datetime.now(UTC),
            op=op,
            result=result,
            store_key=store_key,
            content_hash=content_hash,
            size=size,
            source=source,
            subject="service:lyra-blobstore",
            kind="blobs.op",
        )
        await sink.emit(event)
    except (OSError, RuntimeError, TypeError, ValueError):
        pass


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _store(request: Request) -> FsBlobStore:
    return request.app.state.store  # type: ignore[no-any-return]


def _conn(store: FsBlobStore) -> aiosqlite.Connection:
    if store._conn is None:  # noqa: SLF001
        raise RuntimeError("FsBlobStore not open")
    return store._conn  # noqa: SLF001


async def _http_guard(
    request: Request,
    op: Literal["put", "get", "delete"],
    store_key: str | None,
    handler: Callable[[], Awaitable[_R]],
) -> _R:
    """Run a blob handler; map unexpected failures to HTTP 500 once."""
    try:
        return await handler()
    except BlobNotFoundError:
        await _emit_audit(
            request.app, op=op, result="not_found", store_key=store_key
        )
        return JSONResponse({"detail": "blob not found"}, status_code=404)  # type: ignore[return-value]
    except BlobWriteError:
        _log.exception("%s /blobs write failed", op.upper())
        await _emit_audit(
            request.app,
            op=op,
            result="write_failed",
            store_key=store_key,
        )
        return JSONResponse({"detail": "blob write failed"}, status_code=500)  # type: ignore[return-value]
    except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: blobstore-http — unhandled errors map to 500
        _log.exception("%s /blobs unexpected error", op.upper())
        await _emit_audit(
            request.app,
            op=op,
            result="internal_error",
            store_key=store_key,
        )
        return JSONResponse({"detail": "internal error"}, status_code=500)  # type: ignore[return-value]


# ---------------------------------------------------------------------------
# N1 — PUT /blobs
# ---------------------------------------------------------------------------


async def handle_put(request: Request) -> JSONResponse:
    source = request.headers.get("X-Blob-Source")
    if not source:
        return JSONResponse(  # 400s not in audit scope for V8
            {"detail": "X-Blob-Source header is required"}, status_code=400
        )

    platform_ref = request.headers.get("X-Blob-Platform-Ref")
    platform_message_id = request.headers.get("X-Blob-Platform-Message-Id")
    filename = request.headers.get("X-Blob-Filename")
    raw_ct = request.headers.get("Content-Type", "application/octet-stream")
    mime = raw_ct.split(";")[0].strip() or "application/octet-stream"  # strip params

    async def _put() -> JSONResponse:
        body = await request.body()
        store = _store(request)
        ref = await store.put(
            body,
            mime=mime,
            source=source,
            filename=filename,
            platform_ref=platform_ref,
            platform_message_id=platform_message_id,
        )
        wire_key = f"sha256:{ref.content_hash}"
        payload = ref.model_dump(mode="json")
        payload["store_key"] = wire_key  # on-disk store_path stays internal
        await _emit_audit(request.app, op="put", result="ok", store_key=wire_key)
        return JSONResponse(
            payload, status_code=201, headers={"Location": f"/blobs/{wire_key}"}
        )

    return await _http_guard(request, "put", None, _put)


# ---------------------------------------------------------------------------
# N2 — GET /blobs/{store_key}
# ---------------------------------------------------------------------------


async def handle_get(store_key: str, request: Request) -> Response:
    async def _get() -> Response:
        store = _store(request)
        resolved = await resolve_wire_key(store, store_key)
        data = await store.get(resolved)
        await _emit_audit(request.app, op="get", result="ok", store_key=store_key)
        return Response(content=data, media_type="application/octet-stream")

    return await _http_guard(request, "get", store_key, _get)


# ---------------------------------------------------------------------------
# N3 — HEAD /blobs/{store_key}
# ---------------------------------------------------------------------------


async def handle_head(store_key: str, request: Request) -> Response:
    store = _store(request)
    try:
        conn = _conn(store)
    except RuntimeError:
        return JSONResponse({"detail": "internal error"}, status_code=500)

    try:
        cursor = await conn.execute(
            "SELECT content_hash FROM blobs WHERE store_path = ?",
            (store_key,),
        )
        row = await cursor.fetchone()
        await cursor.close()
        if row is None:
            # Fallback: content_hash form (bare hex or sha256: prefix).
            cursor2 = await conn.execute(
                "SELECT content_hash FROM blobs WHERE content_hash = ?",
                (store_key.removeprefix("sha256:"),),
            )
            row = await cursor2.fetchone()
            await cursor2.close()
    except aiosqlite.Error:
        _log.exception("HEAD /blobs/%s db lookup failed", store_key)
        await _emit_audit(
            request.app, op="exists", result="internal_error", store_key=store_key
        )
        return JSONResponse({"detail": "internal error"}, status_code=500)

    if row is None:
        await _emit_audit(
            request.app, op="exists", result="not_found", store_key=store_key
        )
        return Response(status_code=404)

    # Row confirms existence: ≤2 SELECTs, 0 calls to store.exists() (consensus T3).
    await _emit_audit(
        request.app,
        op="exists",
        result="ok",
        store_key=store_key,
        content_hash=str(row[0]),
    )
    return Response(status_code=200)


# ---------------------------------------------------------------------------
# N4 — DELETE /blobs/{key}
# ---------------------------------------------------------------------------


async def handle_delete(key: str, request: Request) -> Response:
    store = _store(request)

    if _INT_KEY_RE.match(key):
        blob_ref_id = int(key)
    else:
        try:
            conn = _conn(store)
        except RuntimeError:
            return JSONResponse({"detail": "internal error"}, status_code=500)
        try:
            resolved_id = await resolve_delete_key(conn, key)
        except aiosqlite.Error:
            _log.exception("DELETE /blobs/%s db lookup failed", key)
            await _emit_audit(
                request.app, op="delete", result="internal_error", store_key=key
            )
            return JSONResponse({"detail": "internal error"}, status_code=500)

        if resolved_id is None:
            await _emit_audit(
                request.app, op="delete", result="not_found", store_key=key
            )
            return JSONResponse({"detail": "blob not found"}, status_code=404)

        blob_ref_id = resolved_id

    # Verify existence before calling delete() (delete() silently no-ops on miss)
    try:
        conn2 = _conn(store)
        cursor2 = await conn2.execute(
            "SELECT id FROM blob_refs WHERE id = ?", (blob_ref_id,)
        )
        exists_row = await cursor2.fetchone()
        await cursor2.close()
    except RuntimeError:
        return JSONResponse({"detail": "internal error"}, status_code=500)
    except aiosqlite.Error:
        _log.exception("DELETE /blobs/%s pre-check failed", key)
        await _emit_audit(
            request.app, op="delete", result="internal_error", store_key=key
        )
        return JSONResponse({"detail": "internal error"}, status_code=500)

    # DELETE is idempotent (RFC 9110 §9.3.5) — the 204↔404 TOCTOU window between
    # the existence check and the unlink is safe; concurrent deletes converge.
    if exists_row is None:
        await _emit_audit(request.app, op="delete", result="not_found", store_key=key)
        return JSONResponse({"detail": "blob not found"}, status_code=404)

    async def _delete() -> Response:
        await store.delete(blob_ref_id)
        await _emit_audit(request.app, op="delete", result="ok", store_key=key)
        return Response(status_code=204)

    return await _http_guard(request, "delete", key, _delete)
