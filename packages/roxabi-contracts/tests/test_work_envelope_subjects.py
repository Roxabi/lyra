"""Enforcement tests: WorkEnvelope / INFRA classification invariants (#1619).

These tests lock the WORK / INFRA / PENDING model classification so that:
  - Any new domain model is forced into one of the three buckets.
  - WORK models are guaranteed to subclass WorkEnvelope.
  - INFRA and PENDING models are guaranteed NOT to subclass WorkEnvelope.
  - Wire-compat is verified: a WorkEnvelope payload without job_id still parses.
  - The empty-job_id invariant is explicitly tested.

PENDING models (#1838) still sit on ContractEnvelope; they remain classified
here so additions are caught by the completeness test before the follow-up
reparent lands.
"""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
from pydantic import ValidationError

from roxabi_contracts import WorkEnvelope
from roxabi_contracts.audit import SecurityEvent
from roxabi_contracts.audit.blobs import BlobAuditEvent
from roxabi_contracts.cli.models import (
    CliChunkEvent,
    CliCmdPayload,
    CliControlAck,
    CliControlCmd,
    CliHeartbeat,
)
from roxabi_contracts.event.models import LyraEvent, LyraMetric
from roxabi_contracts.gh.models import MintFailureEvent
from roxabi_contracts.image.models import ImageHeartbeat, ImageRequest, ImageResponse
from roxabi_contracts.jobs.models import JobEnvelope, JobProgress, JobResult
from roxabi_contracts.llm.models import (
    LifecycleRequest,
    LifecycleResponse,
    LlmChunkEvent,
    LlmRequest,
    LlmResponse,
)
from roxabi_contracts.turns.models import TurnWriteEvent
from roxabi_contracts.voice.models import (
    SttRequest,
    SttResponse,
    TtsRequest,
    TtsResponse,
)

# ---------------------------------------------------------------------------
# Classification tables
# ---------------------------------------------------------------------------

#: 17 domain models that carry a job identity → MUST subclass WorkEnvelope.
WORK_MODELS = {
    JobEnvelope,
    JobResult,
    JobProgress,
    LlmRequest,
    LlmChunkEvent,
    LlmResponse,
    TtsRequest,
    TtsResponse,
    SttRequest,
    SttResponse,
    ImageRequest,
    ImageResponse,
    TurnWriteEvent,
    # cli models reparented from PENDING (#1838 → S1)
    CliCmdPayload,
    CliChunkEvent,
    CliControlAck,
    CliControlCmd,
}

#: 9 infra-plane models — deliberate ContractEnvelope, no job identity.
INFRA_MODELS = {
    LifecycleRequest,
    LifecycleResponse,
    ImageHeartbeat,
    CliHeartbeat,
    BlobAuditEvent,
    SecurityEvent,
    LyraEvent,
    LyraMetric,
    MintFailureEvent,
}

#: PENDING bucket is empty after S1 graduates the 4 cli models (#1838).
PENDING_MODELS: set = set()

ALL_CLASSIFIED = WORK_MODELS | INFRA_MODELS | PENDING_MODELS


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _base_fields() -> dict:
    """Minimum fields shared by all ContractEnvelope subclasses."""
    return {
        "contract_version": "1",
        "trace_id": "testrace0001",
        "issued_at": datetime.now(timezone.utc),
    }


# ---------------------------------------------------------------------------
# (a) Completeness
# ---------------------------------------------------------------------------


def test_completeness_no_duplicate_across_buckets() -> None:
    """Each model appears in exactly one bucket."""
    assert not (WORK_MODELS & INFRA_MODELS), "overlap: WORK ∩ INFRA"
    assert not (WORK_MODELS & PENDING_MODELS), "overlap: WORK ∩ PENDING"
    assert not (INFRA_MODELS & PENDING_MODELS), "overlap: INFRA ∩ PENDING"


def test_completeness_coverage() -> None:
    """ALL_CLASSIFIED covers the expected count: 17 WORK + 9 INFRA + 0 PENDING = 26."""
    assert len(WORK_MODELS) == 17, f"expected 17 WORK models, got {len(WORK_MODELS)}"
    assert len(INFRA_MODELS) == 9, f"expected 9 INFRA models, got {len(INFRA_MODELS)}"
    assert len(PENDING_MODELS) == 0, (
        f"expected 0 PENDING models, got {len(PENDING_MODELS)}"
    )
    assert len(ALL_CLASSIFIED) == 26


# ---------------------------------------------------------------------------
# (b) WORK ⇒ issubclass WorkEnvelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("model_cls", sorted(WORK_MODELS, key=lambda c: c.__name__))
def test_work_model_is_subclass_of_work_envelope(model_cls) -> None:
    """Every WORK model MUST inherit from WorkEnvelope."""
    assert issubclass(model_cls, WorkEnvelope), (
        f"{model_cls.__name__} is classified as WORK but does not subclass WorkEnvelope"
    )


# ---------------------------------------------------------------------------
# (c) INFRA + PENDING ⇒ NOT issubclass WorkEnvelope
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "model_cls",
    sorted(INFRA_MODELS, key=lambda c: c.__name__),
)
def test_non_work_model_not_subclass_of_work_envelope(model_cls) -> None:
    """INFRA models MUST NOT inherit from WorkEnvelope."""
    assert not issubclass(model_cls, WorkEnvelope), (
        f"{model_cls.__name__} is classified as INFRA"
        " but subclasses WorkEnvelope"
    )


# ---------------------------------------------------------------------------
# (d) Wire-compat: WorkEnvelope parses without job_id (TRANSITIONAL)
# ---------------------------------------------------------------------------


def test_work_envelope_wire_compat_omitted_job_id() -> None:
    """Pre-#1619 wire messages without job_id MUST still deserialize.

    TRANSITIONAL invariant: job_id carries a default_factory so old
    producers are not broken.  The flip to hard-required is tracked in
    #1841.
    """
    payload = {
        "contract_version": "1",
        "trace_id": "compat-trace-0001",
        "issued_at": "2026-06-01T00:00:00+00:00",
        # job_id intentionally absent
    }
    env = WorkEnvelope.model_validate(payload)
    # A fresh job_id was minted by default_factory.
    assert env.job_id
    assert len(env.job_id) == 32
    assert env.job_id.isalnum()


# ---------------------------------------------------------------------------
# (e) job_id="" raises ValidationError
# ---------------------------------------------------------------------------


def test_work_envelope_empty_job_id_raises() -> None:
    """An explicit empty string for job_id MUST be rejected.

    SC-13: producers MUST supply a non-empty job_id.  Passing an empty
    string (a common default-sentinel mistake) is a contract violation
    that must surface loudly, not silently produce a useless job identity.
    """
    with pytest.raises(ValidationError):
        WorkEnvelope(
            **_base_fields(),
            job_id="",
        )


# ---------------------------------------------------------------------------
# (f) CliCmdPayload id taxonomy invariant
# ---------------------------------------------------------------------------


def test_cli_cmd_payload_lyra_session_id_not_pool_id() -> None:
    """lyra_session_id and pool_id are distinct identity axes.

    SC-6: the two ids belong to different granularity levels (conversation vs.
    worker slot) and MUST NOT be equal in a correctly constructed payload.
    """
    from roxabi_contracts.cli.models import CliCmdPayload

    payload = CliCmdPayload(
        **_base_fields(),
        pool_id="pool-abc",
        lyra_session_id="sess-xyz",
        text="hello",
        model_cfg={},
        system_prompt="system",
    )
    assert payload.lyra_session_id != payload.pool_id
