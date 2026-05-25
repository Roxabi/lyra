"""HTTP handler implementations for the blobstore service (N1–N4).

Extracted from `serve.py` to satisfy the 300-line / complexity limits.
These are *not* a public API — import only from `serve.py`.
"""

from __future__ import annotations

import logging
import re
from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

import aiosqlite
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response

from roxabi_blobs import FsBlobStore
from roxabi_blobs.errors import BlobNotFoundError, BlobWriteError
from roxabi_contracts.audit.blobs import BlobAuditEvent
from roxabi_contracts.envelope import CONTRACT_VERSION

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
    except Exception:  # noqa: BLE001
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


# ---------------------------------------------------------------------------
# N1 — PUT /blobs
# ---------------------------------------------------------------------------


async def handle_put(request: Request) -> JSONResponse:
    source = request.headers.get("X-Blob-Source")
    if not source:
        # 400s are not in audit scope for V8 (only the 5 result Literals are).
        return JSONResponse(
            {"detail": "X-Blob-Source header is required"}, status_code=400
        )

    platform_ref = request.headers.get("X-Blob-Platform-Ref")
    platform_message_id = request.headers.get("X-Blob-Platform-Message-Id")
    filename = request.headers.get("X-Blob-Filename")
    raw_ct = request.headers.get("Content-Type", "application/octet-stream")
    # Strip parameters: "image/png; charset=utf-8" → "image/png"
    mime = raw_ct.split(";")[0].strip() or "application/octet-stream"

    body = await request.body()
    store = _store(request)

    try:
        ref = await store.put(
            body,
            mime=mime,
            source=source,
            filename=filename,
            platform_ref=platform_ref,
            platform_message_id=platform_message_id,
        )
    except BlobWriteError:
        _log.exception("PUT /blobs write failed")
        await _emit_audit(request.app, op="put", result="write_failed")
        return JSONResponse({"detail": "blob write failed"}, status_code=500)
    except Exception:  # noqa: BLE001 — spec: all unhandled → 500, no leak
        _log.exception("PUT /blobs unexpected error")
        await _emit_audit(request.app, op="put", result="internal_error")
        return JSONResponse({"detail": "internal error"}, status_code=500)

    payload = ref.model_dump(mode="json")
    # ref.id is populated by FsBlobStore.put via lastrowid (#1330 T8).
    await _emit_audit(request.app, op="put", result="ok", store_key=ref.store_key)
    return JSONResponse(
        payload, status_code=201, headers={"Location": f"/blobs/{ref.store_key}"}
    )


# ---------------------------------------------------------------------------
# N2 — GET /blobs/{store_key}
# ---------------------------------------------------------------------------


async def handle_get(store_key: str, request: Request) -> Response:
    store = _store(request)
    try:
        data = await store.get(store_key)
    except BlobNotFoundError:
        await _emit_audit(
            request.app, op="get", result="not_found", store_key=store_key
        )
        return JSONResponse({"detail": "blob not found"}, status_code=404)
    except Exception:  # noqa: BLE001
        _log.exception("GET /blobs/%s unexpected error", store_key)
        await _emit_audit(
            request.app, op="get", result="internal_error", store_key=store_key
        )
        return JSONResponse({"detail": "internal error"}, status_code=500)

    await _emit_audit(request.app, op="get", result="ok", store_key=store_key)
    # Returning raw bytes; per-blob mime requires an extra SQLite lookup —
    # acceptable simplification for V8 scope.
    return Response(content=data, media_type="application/octet-stream")


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
            # Fallback: store_key may be a content_hash (HttpBlobStore.exists passes
            # content_hash directly — see roxabi-blobs CLAUDE.md §HttpBlobStore.exists).
            cursor2 = await conn.execute(
                "SELECT content_hash FROM blobs WHERE content_hash = ?",
                (store_key,),
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

    content_hash = str(row[0])
    try:
        blob_ref = await store.exists(content_hash)
    except Exception:  # noqa: BLE001
        _log.exception("HEAD /blobs/%s exists check failed", store_key)
        await _emit_audit(
            request.app, op="exists", result="internal_error", store_key=store_key
        )
        return JSONResponse({"detail": "internal error"}, status_code=500)

    if blob_ref is None:
        await _emit_audit(
            request.app, op="exists", result="not_found", store_key=store_key
        )
        return Response(status_code=404)

    await _emit_audit(request.app, op="exists", result="ok", store_key=store_key)
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
            cursor = await conn.execute(
                "SELECT r.id FROM blob_refs r "
                "JOIN blobs b ON r.content_hash = b.content_hash "
                "WHERE b.store_path = ? "
                "ORDER BY r.ingested_at DESC LIMIT 1",
                (key,),
            )
            row = await cursor.fetchone()
            await cursor.close()
        except aiosqlite.Error:
            _log.exception("DELETE /blobs/%s db lookup failed", key)
            await _emit_audit(
                request.app, op="delete", result="internal_error", store_key=key
            )
            return JSONResponse({"detail": "internal error"}, status_code=500)

        if row is None:
            await _emit_audit(
                request.app, op="delete", result="not_found", store_key=key
            )
            return JSONResponse({"detail": "blob not found"}, status_code=404)

        blob_ref_id = int(row[0])

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

    if exists_row is None:
        await _emit_audit(request.app, op="delete", result="not_found", store_key=key)
        return JSONResponse({"detail": "blob not found"}, status_code=404)

    try:
        await store.delete(blob_ref_id)
    except BlobWriteError:
        _log.exception("DELETE /blobs/%s write failed", key)
        await _emit_audit(
            request.app, op="delete", result="write_failed", store_key=key
        )
        return JSONResponse({"detail": "blob write failed"}, status_code=500)
    except Exception:  # noqa: BLE001
        _log.exception("DELETE /blobs/%s unexpected error", key)
        await _emit_audit(
            request.app, op="delete", result="internal_error", store_key=key
        )
        return JSONResponse({"detail": "internal error"}, status_code=500)

    await _emit_audit(request.app, op="delete", result="ok", store_key=key)
    return Response(status_code=204)
