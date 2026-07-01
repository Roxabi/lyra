"""SocialMediaCodec — encode/decode for factory.tool.socialmedia.* wire payloads."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from factory.nats.envelope_fields import mint_work_envelope_fields
from factory.transport._result import Err, Result, SanitizedError
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
    def envelope_fields(
        self,
        *,
        trace_id: str | None = None,
        job_id: str | None = None,
        parent_job_id: str | None = None,
        pool_id: str | None = None,
    ) -> dict:
        return mint_work_envelope_fields(
            trace_id=trace_id,
            job_id=job_id,
            parent_job_id=parent_job_id,
            pool_id=pool_id,
        ).as_dict()

    def encode(self, request: BaseModel) -> bytes:
        return request.model_dump_json(exclude_none=True).encode("utf-8")

    def decode(self, result: Result[bytes, SanitizedError], model: type[TResponse]) -> SocialMediaResult:  # noqa: E501
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