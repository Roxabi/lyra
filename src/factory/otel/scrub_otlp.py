"""Scrub forbidden attributes from OTLP export batches at ingest."""

from __future__ import annotations

from typing import Any

from roxabi_contracts.telemetry import FORBIDDEN_ATTR_PREFIXES, FORBIDDEN_ATTRS


def _drop_attr_key(key: str) -> bool:
    low = key.lower()
    if low in FORBIDDEN_ATTRS:
        return True
    return any(low.startswith(prefix) for prefix in FORBIDDEN_ATTR_PREFIXES)


def scrub_otlp_dict(payload: dict[str, Any]) -> int:
    """Remove forbidden span attribute keys; return count dropped."""
    dropped = 0
    for resource_span in payload.get("resourceSpans", []):
        if not isinstance(resource_span, dict):
            continue
        for scope_span in resource_span.get("scopeSpans", []):
            if not isinstance(scope_span, dict):
                continue
            for span in scope_span.get("spans", []):
                if not isinstance(span, dict):
                    continue
                attrs = span.get("attributes")
                if not isinstance(attrs, list):
                    continue
                kept: list[Any] = []
                for attr in attrs:
                    if not isinstance(attr, dict):
                        kept.append(attr)
                        continue
                    key = attr.get("key", "")
                    if isinstance(key, str) and _drop_attr_key(key):
                        dropped += 1
                        continue
                    kept.append(attr)
                span["attributes"] = kept
    return dropped