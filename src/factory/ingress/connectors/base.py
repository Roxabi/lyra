"""Shared connector helpers."""

from __future__ import annotations

import re

from factory.ingress.ports import VerificationError

__all__ = ["VerificationError", "validate_factory_tenant"]

_TENANT_SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


def validate_factory_tenant(slug: str) -> str:
    if not _TENANT_SLUG_RE.fullmatch(slug):
        raise VerificationError("invalid tenant slug")
    return slug