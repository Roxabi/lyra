"""Scrub forbidden span attributes before export."""

from __future__ import annotations

from collections.abc import Mapping

from roxabi_contracts.telemetry import FORBIDDEN_ATTR_PREFIXES, FORBIDDEN_ATTRS


def scrub_attrs(
    attrs: Mapping[str, str | int | float | bool],
) -> tuple[dict[str, str | int | float | bool], int]:
    """Return scrubbed attrs and count of dropped keys."""
    out: dict[str, str | int | float | bool] = {}
    dropped = 0
    for key, value in attrs.items():
        low = key.lower()
        if low in FORBIDDEN_ATTRS:
            dropped += 1
            continue
        if any(low.startswith(prefix) for prefix in FORBIDDEN_ATTR_PREFIXES):
            dropped += 1
            continue
        if isinstance(value, str) and len(value) > 2048:
            dropped += 1
            continue
        out[key] = value
    return out, dropped