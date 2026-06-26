"""WorkEnvelope coercion for hub payloads that omit envelope fields."""

from __future__ import annotations

from datetime import datetime, timezone

from roxabi_contracts.envelope import CONTRACT_VERSION


def coerce_envelope_fields(payload: dict) -> dict:
    """Fill required WorkEnvelope fields when the hub omits them."""
    data = dict(payload)
    if not data.get("contract_version"):
        data["contract_version"] = CONTRACT_VERSION
    if not data.get("trace_id"):
        data["trace_id"] = data.get("request_id") or "unknown"
    if "issued_at" not in data:
        data["issued_at"] = datetime.now(timezone.utc)
    return data


def work_fields_from_request(req: object) -> dict:
    """Copy WorkEnvelope correlation fields from a validated request model."""
    return {
        "contract_version": req.contract_version,  # type: ignore[attr-defined]
        "trace_id": req.trace_id,  # type: ignore[attr-defined]
        "issued_at": req.issued_at,  # type: ignore[attr-defined]
        "job_id": req.job_id,  # type: ignore[attr-defined]
    }