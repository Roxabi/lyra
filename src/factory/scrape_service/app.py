"""FastAPI app for the scrape HTTP service (#2327)."""

from __future__ import annotations

import hmac
import logging
import os
from typing import Any

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

from factory.scrape_service.extract import fetch_and_extract
from factory.scrape_service.ssrf import SsrfRejected

log = logging.getLogger(__name__)

_PUBLIC_PATHS = frozenset({"/health", "/ready"})


class ScrapeRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    timeout_s: float = Field(default=30.0, ge=1.0, le=120.0)


class _BearerMiddleware(BaseHTTPMiddleware):
    """Optional bearer when FACTORY_SCRAPE_TOKEN is set (recommended on roxabi)."""

    def __init__(self, app: Any, *, token: str) -> None:
        super().__init__(app)
        self._token = token

    async def dispatch(self, request: Request, call_next: Any) -> Any:
        if request.url.path in _PUBLIC_PATHS:
            return await call_next(request)
        auth = request.headers.get("authorization", "")
        if not auth.startswith("Bearer "):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        got = auth[7:].strip()
        if not hmac.compare_digest(got, self._token):
            return JSONResponse({"detail": "unauthorized"}, status_code=401)
        return await call_next(request)


def _err(reason: str, error: str, status: int) -> JSONResponse:
    return JSONResponse(
        {"success": False, "error": error, "reason": reason},
        status_code=status,
    )


async def _handle_scrape(body: ScrapeRequest) -> JSONResponse:
    try:
        text = await fetch_and_extract(body.url, timeout=body.timeout_s)
    except SsrfRejected as exc:
        return _err("ssrf", str(exc.reason), 400)
    except httpx.TimeoutException:
        return _err("timeout", "timeout", 504)
    except httpx.HTTPError as exc:
        log.info("scrape fetch failed: type=%s", type(exc).__name__)
        return _err("fetch", type(exc).__name__, 502)
    except ValueError as exc:
        return _err("fetch", str(exc), 502)
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: scrape-api
        log.warning("scrape unexpected: type=%s", type(exc).__name__)
        return _err("unavailable", "unavailable", 503)
    return JSONResponse(
        {"success": True, "text": text, "url": body.url.strip()}
    )


def build_app(*, token: str | None = None) -> FastAPI:
    """Create the scrape service application.

    When *token* (or env ``FACTORY_SCRAPE_TOKEN``) is set, ``POST /scrape``
    requires ``Authorization: Bearer``. ``/health`` and ``/ready`` stay open.
    """
    app = FastAPI(title="factory-scrape", version="1.0.0")
    env_tok = os.environ.get("FACTORY_SCRAPE_TOKEN", "")
    resolved = (token if token is not None else env_tok).strip()
    if resolved:
        app.add_middleware(_BearerMiddleware, token=resolved)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        try:
            import importlib

            importlib.import_module("httpx")
            importlib.import_module("factory.scrape_service.extract")
        except ImportError as exc:
            log.warning("scrape ready fail: %s", exc)
            return JSONResponse({"ready": False}, status_code=503)
        return JSONResponse({"ready": True})

    @app.post("/scrape")
    async def scrape(body: ScrapeRequest) -> JSONResponse:
        return await _handle_scrape(body)

    return app
