"""FastAPI app for the scrape HTTP service (#2327)."""

from __future__ import annotations

import logging
from typing import Any

import httpx
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field

from factory.scrape_service.extract import fetch_and_extract
from factory.scrape_service.ssrf import SsrfRejected

log = logging.getLogger(__name__)


class ScrapeRequest(BaseModel):
    url: str = Field(..., min_length=8, max_length=2048)
    timeout_s: float = Field(default=30.0, ge=1.0, le=120.0)


def build_app() -> FastAPI:
    """Create the scrape service application."""
    app = FastAPI(title="factory-scrape", version="1.0.0")

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> JSONResponse:
        # Process + import path OK (extractor is pure-Python + httpx).
        try:
            import httpx  # noqa: F401 — readiness: dep importable

            import factory.scrape_service.extract  # noqa: F401
        except ImportError as exc:
            log.warning("scrape ready fail: %s", exc)
            return JSONResponse({"ready": False}, status_code=503)
        return JSONResponse({"ready": True})

    @app.post("/scrape")
    async def scrape(body: ScrapeRequest) -> JSONResponse:
        try:
            text = await fetch_and_extract(body.url, timeout=body.timeout_s)
        except SsrfRejected as exc:
            return JSONResponse(
                {
                    "success": False,
                    "error": str(exc.reason),
                    "reason": "ssrf",
                },
                status_code=400,
            )
        except httpx.TimeoutException:
            return JSONResponse(
                {
                    "success": False,
                    "error": "timeout",
                    "reason": "timeout",
                },
                status_code=504,
            )
        except httpx.HTTPError as exc:
            log.info("scrape fetch failed: type=%s", type(exc).__name__)
            return JSONResponse(
                {
                    "success": False,
                    "error": type(exc).__name__,
                    "reason": "fetch",
                },
                status_code=502,
            )
        except ValueError as exc:
            return JSONResponse(
                {
                    "success": False,
                    "error": str(exc),
                    "reason": "fetch",
                },
                status_code=502,
            )
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch# boundary: scrape-api — terminal handler; type only
            log.warning("scrape unexpected: type=%s", type(exc).__name__)
            return JSONResponse(
                {
                    "success": False,
                    "error": "unavailable",
                    "reason": "unavailable",
                },
                status_code=503,
            )

        payload: dict[str, Any] = {
            "success": True,
            "text": text,
            "url": body.url.strip(),
        }
        return JSONResponse(payload)

    return app
