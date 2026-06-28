"""Map external webhook payloads to LyraEvent fields."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.event.models import LyraEvent


def github_event_kind(event_name: str, payload: dict[str, Any]) -> str:
    """``check_run`` + action ``completed`` → ``check_run.completed``."""
    event = event_name.strip().replace("-", "_")
    action = payload.get("action")
    if isinstance(action, str) and action:
        return f"{event}.{action}"
    return event


def github_event_level(kind: str, payload: dict[str, Any]) -> str:
    conclusion = _nested_str(payload, "check_run", "conclusion")
    if conclusion == "failure" or kind.endswith(".failure"):
        return "warn"
    status = _nested_str(payload, "workflow_run", "conclusion")
    if status == "failure":
        return "warn"
    return "info"


def github_event_message(kind: str, payload: dict[str, Any]) -> str | None:
    repo = _nested_str(payload, "repository", "full_name")
    if repo:
        return f"github {kind} ({repo})"
    return f"github {kind}"


def cloudflare_event_kind(payload: dict[str, Any]) -> str:
    text = str(payload.get("text", "")).lower()
    name = str(payload.get("name", "")).lower()
    blob = f"{text} {name}"
    if "deployment" in blob and ("fail" in blob or "error" in blob):
        return "pages.deployment.failure"
    if "deployment" in blob and ("success" in blob or "succeeded" in blob):
        return "pages.deployment.success"
    alert_type = payload.get("alert_type")
    if isinstance(alert_type, str) and alert_type:
        return alert_type.replace("-", "_").replace(" ", "_")
    return "notification.received"


def cloudflare_event_level(kind: str) -> str:
    if kind.endswith(".failure"):
        return "warn"
    return "info"


def build_lyra_event(  # noqa: PLR0913 — envelope field bundle
    *,
    service: str,
    kind: str,
    level: str,
    message: str | None,
    payload: dict[str, Any],
    trace_id: str | None = None,
) -> LyraEvent:
    return LyraEvent(
        contract_version=CONTRACT_VERSION,
        trace_id=trace_id or uuid4().hex,
        issued_at=datetime.now(tz=UTC),
        service=service,
        kind=kind,
        level=level,  # type: ignore[arg-type]
        message=message,
        payload=payload,
    )


def _nested_str(payload: dict[str, Any], *keys: str) -> str | None:
    node: Any = payload
    for key in keys:
        if not isinstance(node, dict):
            return None
        node = node.get(key)
    return node if isinstance(node, str) else None