"""Operator session auth for dashboard BFF (#1992 phase 2 minimal)."""

from __future__ import annotations

import hmac
import os
from dataclasses import dataclass

from fastapi import Header, HTTPException


@dataclass(frozen=True, slots=True)
class OperatorContext:
    """Authenticated operator principal scoped to one factory_tenant."""

    user_id: str
    factory_tenant: str


def _configured_operator_token() -> str | None:
    raw = os.environ.get("FACTORY_DASHBOARD_OPERATOR_TOKEN", "").strip()
    return raw or None


def default_factory_tenant() -> str:
    raw = os.environ.get("FACTORY_DASHBOARD_FACTORY_TENANT", "default").strip()
    return raw or "default"


def default_operator_user_id() -> str:
    return (
        os.environ.get("FACTORY_DASHBOARD_OPERATOR_USER_ID", "operator").strip()
        or "operator"
    )


def require_operator(
    authorization: str | None = Header(default=None),
) -> OperatorContext:
    """Validate bearer token when configured; otherwise allow Tailnet-only dev."""
    token = _configured_operator_token()
    tenant = default_factory_tenant()
    user_id = default_operator_user_id()
    if token is None:
        return OperatorContext(user_id=user_id, factory_tenant=tenant)
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="operator auth required")
    presented = authorization.removeprefix("Bearer ").strip()
    if not hmac.compare_digest(presented, token):
        raise HTTPException(status_code=401, detail="invalid operator token")
    return OperatorContext(user_id=user_id, factory_tenant=tenant)