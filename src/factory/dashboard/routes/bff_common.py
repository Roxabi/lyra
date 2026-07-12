"""Shared helpers for dashboard BFF route modules."""

from __future__ import annotations

import json

from fastapi import HTTPException
from pydantic import ValidationError

from factory.dashboard.hub_client import HubForbiddenError, HubUnauthorizedError


def hub_unavailable(exc: Exception) -> HTTPException:
    return HTTPException(status_code=503, detail=str(exc))


def hub_validation_error(exc: Exception) -> HTTPException:
    return HTTPException(status_code=502, detail=str(exc))


def map_hub_errors(
    exc: Exception,
    *,
    not_found: type[Exception] | None = None,
    conflict: type[Exception] | None = None,
    store_unavailable: type[Exception] | None = None,
) -> HTTPException | None:
    if isinstance(exc, HubUnauthorizedError):
        return HTTPException(status_code=401, detail=str(exc))
    if isinstance(exc, HubForbiddenError):
        return HTTPException(status_code=403, detail=str(exc))
    if not_found is not None and isinstance(exc, not_found):
        return HTTPException(status_code=404, detail=str(exc))
    if conflict is not None and isinstance(exc, conflict):
        return HTTPException(status_code=409, detail=str(exc))
    if store_unavailable is not None and isinstance(exc, store_unavailable):
        return HTTPException(status_code=503, detail=str(exc))
    if isinstance(exc, RuntimeError):
        return hub_unavailable(exc)
    if isinstance(exc, (ValidationError, json.JSONDecodeError)):
        return hub_validation_error(exc)
    return None
