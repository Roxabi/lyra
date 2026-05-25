"""TurnWriter health + metrics HTTP endpoints (FastAPI/uvicorn).

Exposes:
  GET /health  — 200 OK iff writer task alive + NATS connected + store open.
                 503 otherwise. JSON body with status and reasons.
  GET /metrics — Prometheus-text exposition of turn_writer_lag_seconds gauge.

Start via TurnWriterHealthServer.start() / stop(). The FastAPI app is
accessible as .app for testing via httpx.ASGITransport (no real socket needed).
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from fastapi import FastAPI
from fastapi.responses import PlainTextResponse, Response

if TYPE_CHECKING:
    from nats.aio.client import Client as NATSClient

    from lyra.infrastructure.stores.turn_store import TurnStore
    from lyra.infrastructure.turn_writer.writer import TurnWriter

log = logging.getLogger(__name__)

_METRICS_HELP = (
    "# HELP turn_writer_lag_seconds"
    " Seconds since oldest pending unacked message"
)
_METRICS_TYPE = "# TYPE turn_writer_lag_seconds gauge"


class TurnWriterHealthServer:
    """Serve /health and /metrics for the turn-writer process.

    Args:
        writer: The TurnWriter instance (may be None before start).
        store:  The TurnStore instance; open iff store._db is not None.
        nc:     NATS client; healthy iff nc.is_connected is True.
        host:   Bind host (default ``0.0.0.0``).
        port:   Bind port (default ``8083``).
    """

    def __init__(
        self,
        writer: "TurnWriter",
        store: "TurnStore",
        nc: "NATSClient",
        host: str = "0.0.0.0",
        port: int = 8083,
    ) -> None:
        self._writer = writer
        self._store = store
        self._nc = nc
        self._host = host
        self._port = port
        self._server_task: asyncio.Task[None] | None = None
        self.app = self._build_app()

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """Start uvicorn server as a background task."""
        import uvicorn

        config = uvicorn.Config(
            self.app,
            host=self._host,
            port=self._port,
            log_level="warning",
        )
        server = uvicorn.Server(config)
        self._server_task = asyncio.create_task(
            server.serve(), name="turn-writer-health"
        )
        log.info(
            "turn-writer health server started on %s:%d", self._host, self._port
        )

    async def stop(self) -> None:
        """Cancel the health server task."""
        if self._server_task is not None:
            self._server_task.cancel()
            try:
                await self._server_task
            except asyncio.CancelledError:
                pass
            self._server_task = None
        log.info("turn-writer health server stopped")

    # ------------------------------------------------------------------
    # Internal probes
    # ------------------------------------------------------------------

    def _check_writer_alive(self) -> bool:
        """True iff the writer consume task exists and has not completed."""
        task = self._writer._task
        return task is not None and not task.done()

    def _check_nats_connected(self) -> bool:
        """True iff the NATS client reports connected."""
        try:
            return bool(self._nc.is_connected)
        except Exception:  # noqa: BLE001
            return False

    def _check_store_open(self) -> bool:
        """True iff the TurnStore has an open SQLite connection."""
        return self._store._db is not None

    def _compute_lag(self) -> float:
        """Lag in seconds from oldest pending message, or 0.0."""
        oldest = self._writer.oldest_pending
        if oldest is None:
            return 0.0
        return (datetime.now(UTC) - oldest).total_seconds()

    # ------------------------------------------------------------------
    # Health / metrics logic (called by route handlers)
    # ------------------------------------------------------------------

    def _health_payload(self) -> tuple[int, dict[str, Any]]:
        """Return (http_status, body_dict) for the /health endpoint."""
        writer_alive = self._check_writer_alive()
        nats_connected = self._check_nats_connected()
        store_open = self._check_store_open()
        ok = writer_alive and nats_connected and store_open

        reasons: list[str] = []
        if not writer_alive:
            reasons.append("writer_task_not_alive")
        if not nats_connected:
            reasons.append("nats_disconnected")
        if not store_open:
            reasons.append("store_not_open")

        body: dict[str, Any] = {"ok": ok}
        if reasons:
            body["reasons"] = reasons

        status_code = 200 if ok else 503
        return status_code, body

    def _metrics_text(self) -> str:
        """Return Prometheus text-format exposition for turn_writer_lag_seconds."""
        lag = self._compute_lag()
        return f"{_METRICS_HELP}\n{_METRICS_TYPE}\nturn_writer_lag_seconds {lag}\n"

    # ------------------------------------------------------------------
    # App construction
    # ------------------------------------------------------------------

    def _build_app(self) -> FastAPI:
        app = FastAPI(title="Lyra TurnWriter")

        @app.get("/health")
        async def health() -> Response:
            import json

            status_code, body = self._health_payload()
            return Response(
                content=json.dumps(body),
                status_code=status_code,
                media_type="application/json",
            )

        @app.get("/metrics")
        async def metrics() -> PlainTextResponse:
            return PlainTextResponse(self._metrics_text())

        return app
