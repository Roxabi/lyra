"""GitHub-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.

Canonical subject for MintFailureEvent: ``lyra.gh.mint_failure.<machine>``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from roxabi_contracts._nats_utils import validate_job_token
from roxabi_contracts.envelope import ContractEnvelope

__all__ = ["MintFailureEvent"]


class MintFailureEvent(ContractEnvelope):
    """GitHub App token mint failure event.

    Canonical subject: ``lyra.gh.mint_failure.<machine>``.
    Published by a satellite when it fails to obtain a GitHub App installation
    token. The ``machine`` field identifies the emitting host; ``reason``
    carries a short failure label; ``http_status`` is set when the failure
    originated from a GitHub API response, None for pre-API failures
    (timeout, PEM unreadable, etc.).
    """

    machine: str
    """Host identifier, e.g. ``"M1"`` or ``"roxabituwer"``.
    Same charset rules as job_id: alphanumeric, hyphens, underscores,
    internal dots allowed for namespacing — no wildcards, no boundary dots.
    """

    reason: Annotated[str, StringConstraints(min_length=1)]
    """Short failure label.
    Examples: ``"github_api_401"``, ``"github_api_404"``,
    ``"timeout"``, ``"pem_unreadable"``.
    """

    http_status: int | None = Field(default=None, ge=100, le=599)
    """HTTP status from GitHub API, or None for pre-API failures."""

    retries: Annotated[int, Field(ge=0, le=10)] = 0
    """Count of attempts made since last success."""

    @field_validator("machine")
    @classmethod
    def _validate_machine(cls, v: str) -> str:
        validate_job_token(v)
        return v
