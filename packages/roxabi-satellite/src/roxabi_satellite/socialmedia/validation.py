"""Ingress validation for ``factory.tool.socialmedia.*`` requests."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TypeVar

from pydantic import BaseModel, ValidationError
from roxabi_contracts.socialmedia.models import (
    SocialMediaListGroupsRequest,
    SocialMediaListIntegrationsRequest,
    SocialMediaPublishRequest,
    SocialMediaScheduleRequest,
)

from roxabi_satellite.envelope import coerce_envelope_fields

T = TypeVar("T", bound=BaseModel)


@dataclass(frozen=True)
class ValidationOutcome:
    request: BaseModel | None = None
    error: str | None = None


def _validate_model(model_cls: type[T], payload: dict) -> ValidationOutcome:
    try:
        return ValidationOutcome(request=model_cls.model_validate(coerce_envelope_fields(payload)))
    except (ValidationError, TypeError) as exc:
        return ValidationOutcome(error=str(exc))


def validate_list_groups_request(payload: dict) -> ValidationOutcome:
    return _validate_model(SocialMediaListGroupsRequest, payload)


def validate_list_integrations_request(payload: dict) -> ValidationOutcome:
    return _validate_model(SocialMediaListIntegrationsRequest, payload)


def validate_publish_request(payload: dict) -> ValidationOutcome:
    return _validate_model(SocialMediaPublishRequest, payload)


def validate_schedule_request(payload: dict) -> ValidationOutcome:
    return _validate_model(SocialMediaScheduleRequest, payload)