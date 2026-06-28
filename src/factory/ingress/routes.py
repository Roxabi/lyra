"""HTTP route handlers for factory-ingress."""

from __future__ import annotations

import json
import logging
from typing import Any

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse

from factory.ingress.config import IngressConfig
from factory.ingress.normalize import (
    build_lyra_event,
    cloudflare_event_kind,
    cloudflare_event_level,
    github_event_kind,
    github_event_level,
    github_event_message,
)
from factory.ingress.payload import (
    summarize_cloudflare_payload,
    summarize_github_payload,
)
from factory.ingress.publisher import EventPublisher
from factory.ingress.verify import verify_cloudflare_auth, verify_github_signature

log = logging.getLogger(__name__)


def build_router(cfg: IngressConfig, get_publisher) -> APIRouter:
    router = APIRouter()

    @router.get("/health")
    async def health() -> dict[str, bool | str]:
        return {
            "ok": True,
            "github": cfg.github.enabled,
            "cloudflare": cfg.cloudflare.enabled,
        }

    @router.post("/webhook/github")
    async def webhook_github(request: Request) -> JSONResponse:
        if not cfg.github.enabled:
            raise HTTPException(status_code=503, detail="github connector disabled")
        body = await request.body()
        signature = request.headers.get("X-Hub-Signature-256")
        if not verify_github_signature(body, signature, cfg.github.webhook_secret):
            raise HTTPException(status_code=401, detail="invalid signature")
        event_name = request.headers.get("X-GitHub-Event", "unknown")
        delivery_id = request.headers.get("X-GitHub-Delivery", "")
        payload = _parse_json_object(body)
        kind = github_event_kind(event_name, payload)
        lyra = build_lyra_event(
            service="github",
            kind=kind,
            level=github_event_level(kind, payload),
            message=github_event_message(kind, payload),
            payload=summarize_github_payload(payload),
            trace_id=delivery_id or None,
        )
        publisher: EventPublisher | None = get_publisher()
        if publisher is not None:
            await publisher.publish(lyra, msg_id=delivery_id or None)
        return JSONResponse({"accepted": True, "subject_kind": kind}, status_code=202)

    @router.post("/webhook/cloudflare")
    async def webhook_cloudflare(request: Request) -> JSONResponse:
        if not cfg.cloudflare.enabled:
            raise HTTPException(status_code=503, detail="cloudflare connector disabled")
        auth = request.headers.get("cf-webhook-auth")
        if not verify_cloudflare_auth(auth, cfg.cloudflare.webhook_secret):
            raise HTTPException(status_code=401, detail="invalid auth")
        payload = _parse_json_object(await request.body())
        kind = cloudflare_event_kind(payload)
        lyra = build_lyra_event(
            service="cloudflare",
            kind=kind,
            level=cloudflare_event_level(kind),
            message=f"cloudflare {kind}",
            payload=summarize_cloudflare_payload(payload),
        )
        publisher: EventPublisher | None = get_publisher()
        if publisher is not None:
            await publisher.publish(lyra)
        return JSONResponse({"accepted": True, "subject_kind": kind}, status_code=202)

    return router


def _parse_json_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected object payload")
    return payload