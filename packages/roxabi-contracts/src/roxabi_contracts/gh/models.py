"""GitHub-domain NATS contract models.

Pure Pydantic. No NATS imports. No transport logic. Every model subclasses
ContractEnvelope, which provides (contract_version, trace_id, issued_at)
plus ConfigDict(extra="ignore") for forward-compat.

Canonical subject for MintFailureEvent: ``factory.gh.mint_failure.<machine>``.
"""

from __future__ import annotations

from typing import Annotated

from pydantic import Field, StringConstraints, field_validator

from roxabi_contracts._nats_utils import validate_subject_segment
from roxabi_contracts.envelope import ContractEnvelope

__all__ = ["MintFailureEvent"]


class MintFailureEvent(ContractEnvelope):
    """GitHub App token mint failure event.

    Canonical subject: ``factory.gh.mint_failure.<machine>``.
    Published by a satellite when it fails to obtain a GitHub App installation
    token. The ``machine`` field identifies the emitting host; ``reason``
    carries a short failure label; ``http_status`` is set when the failure
    originated from a GitHub API response, None for pre-API failures
    (timeout, PEM unreadable, etc.).
    """

    machine: str
    """Host identifier, e.g. ``"M1"`` or ``"roxabituwer"``.
    A single NATS subject segment: ``[A-Za-z0-9_-]+`` only — no dots,
    wildcards, or ``>``. Dots are rejected so ``machine`` always occupies
    exactly one segment of the 4-segment ``factory.gh.mint_failure.<machine>``
    subject, matching the daemon's ``_safe_machine_name`` sanitizer (#1708).
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
        # machine is a single subject segment, not a dotted job token — reject
        # internal dots so it can never widen the 4-segment subject (#1708).
        validate_subject_segment(v)
        return v
