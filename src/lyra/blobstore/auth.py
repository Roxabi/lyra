"""Bearer auth middleware for the blobstore HTTP service."""

from __future__ import annotations

import hmac
from datetime import datetime, timezone

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from roxabi_contracts.audit.blobs import BlobAuditEvent

_ALLOWLIST = frozenset({"/healthz", "/metrics"})

# Map HTTP method → BlobAuditEvent op literal.
_METHOD_TO_OP: dict[str, str] = {
    "PUT": "put",
    "GET": "get",
    "HEAD": "exists",
    "DELETE": "delete",
}


def _op_from_request(request: Request) -> str:
    return _METHOD_TO_OP.get(request.method.upper(), "get")


def _store_key_from_path(path: str) -> str | None:
    """Extract store_key from /blobs/{store_key}; return None for other paths."""
    if path.startswith("/blobs/"):
        tail = path[len("/blobs/"):]
        return tail if tail else None
    return None


async def _emit_unauthorized_audit(request: Request) -> None:
    """Best-effort audit emission for 401 responses; never raises."""
    sink = getattr(request.app.state, "audit_sink", None)
    if sink is None:
        return
    try:
        event = BlobAuditEvent(
            contract_version="1",
            trace_id="unauthorized",
            issued_at=datetime.now(timezone.utc),
            op=_op_from_request(request),  # type: ignore[arg-type]
            result="unauthorized",
            store_key=_store_key_from_path(request.url.path),
            content_hash=None,
            size=None,
            source=None,
        )
        await sink.emit(event)
    except Exception:  # noqa: BLE001
        pass


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _ALLOWLIST:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            await _emit_unauthorized_audit(request)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

        provided = auth[len("Bearer "):]
        if not hmac.compare_digest(provided.encode(), self._token.encode()):
            await _emit_unauthorized_audit(request)
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

        return await call_next(request)
