"""Tests for roxabi_contracts.jobs domain models and subjects.

Covers SC-10 (model roundtrip, extra-ignore, composite_depth boundary,
JobResult status/error mutex) and SC-11 (subject helpers).
"""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import BaseModel, ValidationError

from roxabi_contracts.errors import WorkerError
from roxabi_contracts.jobs import (
    JobEnvelope,
    JobProgress,
    JobResult,
    jobs_progress,
    jobs_result,
    jobs_submit,
)
from roxabi_contracts.jobs.fixtures import (
    _ENV,
    sample_job_envelope,
    sample_job_progress,
    sample_job_result_err,
    sample_job_result_ok,
)

# ---------------------------------------------------------------------------
# test_roundtrip
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        pytest.param(JobEnvelope, sample_job_envelope, id="JobEnvelope"),
        pytest.param(JobResult, sample_job_result_ok, id="JobResult-success"),
        pytest.param(JobResult, sample_job_result_err, id="JobResult-error"),
        pytest.param(JobProgress, sample_job_progress, id="JobProgress"),
    ],
)
def test_roundtrip(model: type[BaseModel], payload: dict[str, Any]) -> None:
    """model_validate → model_dump_json → model_validate_json yields equal instance."""
    # Arrange / Act
    inst = model.model_validate(payload)
    restored = model.model_validate_json(inst.model_dump_json())

    # Assert
    assert restored == inst


# ---------------------------------------------------------------------------
# test_composite_depth_boundary
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("depth", "should_raise"),
    [
        pytest.param(0, False, id="depth-0-valid"),
        pytest.param(3, False, id="depth-3-valid"),
        pytest.param(4, True, id="depth-4-invalid"),
        pytest.param(-1, True, id="depth--1-invalid"),
    ],
)
def test_composite_depth_boundary(depth: int, should_raise: bool) -> None:
    """composite_depth in [0..3] is valid; 4 or -1 raise ValidationError."""
    # Arrange
    payload: dict[str, Any] = {
        **sample_job_envelope,
        "composite_depth": depth,
    }

    if should_raise:
        with pytest.raises(ValidationError):
            JobEnvelope.model_validate(payload)
    else:
        inst = JobEnvelope.model_validate(payload)
        assert inst.composite_depth == depth


# ---------------------------------------------------------------------------
# test_job_result_invariant
# ---------------------------------------------------------------------------

_WORKER_ERROR = WorkerError(code="worker.crash", message="scraper failed")
_RESULT_BASE: dict[str, Any] = {**_ENV, "job_id": "job-uuid-1234"}


@pytest.mark.parametrize(
    ("kwargs", "should_raise"),
    [
        pytest.param(
            {"status": "error", "error": None},
            True,
            id="error-no-WorkerError",
        ),
        pytest.param(
            {"status": "success", "error": _WORKER_ERROR},
            True,
            id="success-with-error",
        ),
        pytest.param(
            {"status": "success", "data": {"x": 1}},
            False,
            id="success-with-data",
        ),
        pytest.param(
            {"status": "error", "error": _WORKER_ERROR},
            False,
            id="error-with-WorkerError",
        ),
    ],
)
def test_job_result_invariant(kwargs: dict[str, Any], should_raise: bool) -> None:
    """status/error mutex: error=None on error raises; WorkerError on success raises."""
    payload: dict[str, Any] = {**_RESULT_BASE, **kwargs}

    if should_raise:
        with pytest.raises(ValidationError):
            JobResult.model_validate(payload)
    else:
        inst = JobResult.model_validate(payload)
        assert inst.status == kwargs["status"]


# ---------------------------------------------------------------------------
# test_extra_ignore
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("model", "payload"),
    [
        pytest.param(
            JobEnvelope,
            {**sample_job_envelope, "unknown_field": "surprise"},
            id="JobEnvelope-extra",
        ),
        pytest.param(
            JobResult,
            {**sample_job_result_ok, "unknown_field": "surprise"},
            id="JobResult-extra",
        ),
        pytest.param(
            JobProgress,
            {**sample_job_progress, "unknown_field": "surprise"},
            id="JobProgress-extra",
        ),
    ],
)
def test_extra_ignore(model: type[BaseModel], payload: dict[str, Any]) -> None:
    """Unknown fields are silently ignored (extra='ignore') and absent from output."""
    # Arrange / Act
    inst = model.model_validate(payload)
    dumped = inst.model_dump()

    # Assert
    assert "unknown_field" not in dumped


# ---------------------------------------------------------------------------
# test_model_rejects_bad_job_token (B1 — model-level field_validator coverage)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("overrides",),
    [
        pytest.param({"job_id": "bad*id"}, id="envelope-job_id-wildcard"),
        pytest.param({"job_name": "bad>name"}, id="envelope-job_name-wildcard"),
        pytest.param({"reply_to": "bad*inbox"}, id="envelope-reply_to-wildcard"),
    ],
)
def test_job_envelope_rejects_bad_token(overrides: dict[str, Any]) -> None:
    """field_validators on job_id/job_name/reply_to reject NATS wildcards."""
    with pytest.raises(ValidationError):
        JobEnvelope.model_validate({**sample_job_envelope, **overrides})


def test_job_result_rejects_bad_job_id() -> None:
    """field_validator on job_id raises ValidationError for wildcard characters."""
    payload = {**_RESULT_BASE, "job_id": "bad*id", "status": "success"}
    with pytest.raises(ValidationError):
        JobResult.model_validate(payload)


def test_job_progress_rejects_bad_job_id() -> None:
    """field_validator on job_id raises ValidationError for NATS wildcard characters."""
    with pytest.raises(ValidationError):
        JobProgress.model_validate({**sample_job_progress, "job_id": "bad*id"})


# ---------------------------------------------------------------------------
# test_job_progress_pct_boundary (B5)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("pct", "should_raise"),
    [
        pytest.param(0.0, False, id="pct-0-valid"),
        pytest.param(100.0, False, id="pct-100-valid"),
        pytest.param(-0.1, True, id="pct-negative-invalid"),
        pytest.param(100.1, True, id="pct-over-100-invalid"),
        pytest.param(None, False, id="pct-none-valid"),
    ],
)
def test_job_progress_pct_boundary(pct: float | None, should_raise: bool) -> None:
    """pct in [0.0..100.0] is valid; outside that range raises ValidationError."""
    payload: dict[str, Any] = {**sample_job_progress, "pct": pct}

    if should_raise:
        with pytest.raises(ValidationError):
            JobProgress.model_validate(payload)
    else:
        inst = JobProgress.model_validate(payload)
        assert inst.pct == pct


# ---------------------------------------------------------------------------
# test_subjects
# ---------------------------------------------------------------------------


def test_subjects_jobs_submit() -> None:
    """jobs_submit produces lyra.jobs.<job_name>."""
    assert jobs_submit("vault.add-from-url") == "lyra.jobs.vault.add-from-url"


def test_subjects_jobs_result() -> None:
    """jobs_result produces lyra.results.<job_id>."""
    assert jobs_result("job-uuid-1234") == "lyra.results.job-uuid-1234"


def test_subjects_jobs_progress() -> None:
    """jobs_progress produces lyra.progress.<job_id>."""
    assert jobs_progress("job-uuid-1234") == "lyra.progress.job-uuid-1234"


def test_job_envelope_accepts_inbox_reply_to() -> None:
    """_INBOX.nonce subjects are valid reply_to values (underscore in charset)."""
    inst = JobEnvelope.model_validate(
        {**sample_job_envelope, "reply_to": "_INBOX.abc123"}
    )
    assert inst.reply_to == "_INBOX.abc123"


@pytest.mark.parametrize(
    ("helper", "bad_token"),
    [
        pytest.param(jobs_submit, "bad*token", id="submit-asterisk"),
        pytest.param(jobs_submit, "bad>token", id="submit-greater-than"),
        pytest.param(jobs_submit, "", id="submit-empty-string"),
        pytest.param(jobs_result, "bad*token", id="result-asterisk"),
        pytest.param(jobs_result, "bad>token", id="result-greater-than"),
        pytest.param(jobs_result, "", id="result-empty-string"),
        pytest.param(jobs_progress, "bad*token", id="progress-asterisk"),
        pytest.param(jobs_progress, "bad>token", id="progress-greater-than"),
        pytest.param(jobs_progress, "", id="progress-empty-string"),
        pytest.param(jobs_submit, ".leading-dot", id="submit-leading-dot"),
        pytest.param(jobs_submit, "a..b", id="submit-consecutive-dots"),
        pytest.param(jobs_submit, "trailing.", id="submit-trailing-dot"),
    ],
)
def test_subjects_rejects_bad_tokens(helper: Any, bad_token: str) -> None:
    """Subject helpers raise ValueError for wildcards, empty strings, dot boundaries."""
    with pytest.raises(ValueError):
        helper(bad_token)
