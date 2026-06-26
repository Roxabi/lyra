"""SocialMediaCodec — encode/decode for factory.tool.socialmedia.* wire payloads."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypeVar
from uuid import uuid4

from pydantic import BaseModel, ValidationError

from factory.transport._result import Err, Result, SanitizedError
from roxabi_contracts import new_job_id
from roxabi_contracts.envelope import CONTRACT_VERSION
from roxabi_contracts.socialmedia.models import (
    SocialMediaListGroupsResponse,
    SocialMediaListIntegrationsResponse,
    SocialMediaPublishResponse,
)

log = logging.getLogger(__name__)

TRequest = TypeVar("TRequest", bound=BaseModel)
TResponse = TypeVar("TResponse", bound=BaseModel)


@dataclass(frozen=True)
class SocialMediaResult:
    response: BaseModel | None
    error: str = ""


class SocialMediaCodec:
    def envelope_fields(self, *, trace_id: str | None = None) -> dict:
        from datetime import UTC, datetime

        return {
            "contract_version": CONTRACT_VERSION,
            "trace_id": trace_id or str(uuid4()),
            "issued_at": datetime.now(tz=UTC),
            "job_id": new_job_id(),
        }

    def encode(self, request: BaseModel) -> bytes:
        return request.model_dump_json(exclude_none=True).encode("utf-8")

    def decode(self, result: Result[bytes, SanitizedError], model: type[TResponse]) -> SocialMediaResult:
        if isinstance(result, Err):
            return SocialMediaResult(response=None, error=result.error.code)
        try:
            resp = model.model_validate_json(result.value)
        except (ValidationError, ValueError) as exc:
            log.warning("SocialMediaCodec.decode: validation error: %r", exc)
            return SocialMediaResult(response=None, error="decode.validation_error")
        ok = getattr(resp, "ok", True)
        if ok is False:
            err = getattr(resp, "error", None) or "socialmedia.worker_error"
            return SocialMediaResult(response=resp, error=str(err))
        return SocialMediaResult(response=resp, error="")


# Response type aliases for callers
ListGroupsResponse = SocialMediaListGroupsResponse
ListIntegrationsResponse = SocialMediaListIntegrationsResponse
PublishResponse = SocialMediaPublishResponse