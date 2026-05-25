"""Bearer auth middleware for the blobstore HTTP service."""

from __future__ import annotations

import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

_ALLOWLIST = frozenset({"/healthz", "/metrics"})


class BearerAuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next) -> Response:
        if request.url.path in _ALLOWLIST:
            return await call_next(request)

        auth = request.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

        provided = auth[len("Bearer "):]
        if not hmac.compare_digest(provided.encode(), self._token.encode()):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)

        return await call_next(request)
