"""Public NATS identifier validation for satellite CLIs.

Copied from ``roxabi_nats._validate`` so external consumers never import
hub-internal ``_`` modules (ADR-045).
"""

from __future__ import annotations

import re

_NATS_IDENT = re.compile(r"[A-Za-z0-9_.\-]+")
_NATS_SINGLE_TOKEN = re.compile(r"[A-Za-z0-9_\-]+")


def validate_nats_token(value: str, *, kind: str, allow_empty: bool = False) -> None:
    """Raise ``ValueError`` if *value* is not a valid NATS identifier token."""
    if allow_empty and not value:
        return
    if not _NATS_IDENT.fullmatch(value):
        raise ValueError(
            f"Invalid {kind} for NATS: {value!r} — "
            "must match [A-Za-z0-9_.\\-]+ (no wildcards or spaces)"
        )


def validate_nats_single_token(value: str, *, kind: str, allow_empty: bool = False) -> None:
    """Raise ``ValueError`` if *value* is not a valid single NATS token (no dots)."""
    if allow_empty and not value:
        return
    if not _NATS_SINGLE_TOKEN.fullmatch(value):
        raise ValueError(
            f"Invalid {kind} for NATS: {value!r} — "
            "must match [A-Za-z0-9_\\-]+ (no dots, wildcards, or spaces)"
        )