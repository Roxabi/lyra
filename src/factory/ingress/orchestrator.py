"""Webhook ingestion pipeline (ADR-096 normative order)."""

from __future__ import annotations

import json
import logging
from collections.abc import Mapping
from typing import Any

from fastapi import HTTPException
from fastapi.responses import JSONResponse
from pydantic import ValidationError

from factory.ingress.config import IngressConfig
from factory.ingress.connectors.base import validate_factory_tenant
from factory.ingress.connectors.registry import ConnectorRegistry
from factory.ingress.metrics import record_unknown_installation
from factory.ingress.ports import (
    InstallationRegistry,
    SecretResolver,
    VerificationError,
)
from factory.ingress.publisher import EventPublisher

log = logging.getLogger(__name__)

MAX_BODY_BYTES = 5 * 1024 * 1024
_ACCEPTED = JSONResponse({"accepted": True}, status_code=202)


async def handle_webhook(  # noqa: PLR0913, C901
    connector_name: str,
    *,
    headers: Mapping[str, str],
    body: bytes,
    path_tenant: str | None,
    config: IngressConfig,
    registry: ConnectorRegistry,
    installations: InstallationRegistry,
    secrets: SecretResolver,
    publisher: EventPublisher | None,
) -> JSONResponse:
    if len(body) > MAX_BODY_BYTES:
        raise HTTPException(status_code=413, detail="payload too large")

    connector = registry.get(connector_name)
    if connector is None:
        raise HTTPException(status_code=404, detail="unknown connector")

    entry = config.connectors.get(connector_name)
    if entry is None or not entry.enabled:
        raise HTTPException(
            status_code=503, detail=f"{connector_name} connector disabled"
        )

    if connector.family == "per_account":
        if not path_tenant:
            raise HTTPException(status_code=401, detail="invalid auth")
        try:
            validate_factory_tenant(path_tenant)
        except VerificationError as exc:
            raise HTTPException(status_code=401, detail="invalid auth") from exc

    try:
        connector.verify(headers, body, secrets, path_tenant=path_tenant)
    except VerificationError as exc:
        raise HTTPException(status_code=401, detail="invalid auth") from exc

    payload = _parse_json_object(body)
    await connector.apply_lifecycle(headers, payload, installations)

    external_id = connector.parse_external_id(headers, payload, path_tenant=path_tenant)
    factory_tenant = await installations.resolve(connector_name, external_id)

    if factory_tenant is None:
        record_unknown_installation(connector_name)
        log.warning(
            "unknown_installation connector=%s external_id=%s delivery=%s",
            connector_name,
            external_id,
            headers.get("X-GitHub-Delivery", ""),
        )
        return _ACCEPTED

    if connector.family == "per_account" and path_tenant != factory_tenant:
        record_unknown_installation(connector_name)
        log.warning(
            "tenant_mismatch connector=%s path=%s resolved=%s",
            connector_name,
            path_tenant,
            factory_tenant,
        )
        return _ACCEPTED

    try:
        lyra = connector.normalize(headers, payload, tenant=factory_tenant)
    except ValidationError as exc:
        log.warning(
            "normalize_failed connector=%s tenant=%s",
            connector_name,
            factory_tenant,
            exc_info=exc,
        )
        return _ACCEPTED
    kind = lyra.kind
    if publisher is not None:
        delivery_id = headers.get("X-GitHub-Delivery") or None
        await publisher.publish(lyra, msg_id=delivery_id)
    return JSONResponse({"accepted": True, "subject_kind": kind}, status_code=202)


def _parse_json_object(body: bytes) -> dict[str, Any]:
    try:
        payload = json.loads(body)
    except json.JSONDecodeError as exc:
        raise HTTPException(status_code=400, detail="invalid json") from exc
    if not isinstance(payload, dict):
        raise HTTPException(status_code=400, detail="expected object payload")
    return payload