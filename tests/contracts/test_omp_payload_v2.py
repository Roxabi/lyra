"""Wire-compat tests for omp V2 payload convention.

JobEnvelope.payload and JobResult.data are untyped dict[str, Any] — no Pydantic
schema change is needed.  These tests assert that:

  * Old senders (payload = {"prompt": "x"}, no new fields) still validate.
  * New senders adding pool_id + provider_session_id also validate.
  * Old workers (data has no session_file) still validate.
  * New workers returning session_file also validate.

All four cases are wire-compatible: absent fields are tolerated by both sides.
"""

from __future__ import annotations

from typing import Any

import pytest

from roxabi_contracts.jobs import JobEnvelope, JobResult
from roxabi_contracts.jobs.fixtures import ENV_BASE

pytestmark = pytest.mark.omp_contract

# ---------------------------------------------------------------------------
# Shared base payloads (minimal valid envelopes)
# ---------------------------------------------------------------------------

_ENVELOPE_BASE: dict[str, Any] = {
    **ENV_BASE,
    "job_id": "job-omp-v2-test",
    "job_name": "omp.run",
    "reply_to": "_INBOX.omp-v2-test",
}

_RESULT_BASE: dict[str, Any] = {
    **ENV_BASE,
    "job_id": "job-omp-v2-test",
    "status": "success",
}

# ---------------------------------------------------------------------------
# JobEnvelope.payload wire-compat
# ---------------------------------------------------------------------------


def test_envelope_payload_old_style_no_routing_hints() -> None:
    """Old senders omitting pool_id/provider_session_id still validate (V1 compat)."""
    inst = JobEnvelope.model_validate(
        {**_ENVELOPE_BASE, "payload": {"prompt": "hello world"}}
    )
    assert inst.payload == {"prompt": "hello world"}
    assert "pool_id" not in inst.payload
    assert "provider_session_id" not in inst.payload


def test_envelope_payload_v2_with_pool_id_and_session_id() -> None:
    """New senders adding pool_id + provider_session_id validate without error."""
    inst = JobEnvelope.model_validate(
        {
            **_ENVELOPE_BASE,
            "payload": {
                "prompt": "hello world",
                "pool_id": "clipool-abc123",
                "provider_session_id": "sess-xyz987",
            },
        }
    )
    assert inst.payload["pool_id"] == "clipool-abc123"
    assert inst.payload["provider_session_id"] == "sess-xyz987"
    assert inst.payload["prompt"] == "hello world"


# ---------------------------------------------------------------------------
# JobResult.data wire-compat
# ---------------------------------------------------------------------------


def test_result_data_without_session_file() -> None:
    """Old workers returning data without session_file still validate (V1 compat)."""
    inst = JobResult.model_validate({**_RESULT_BASE, "data": {"output": "done"}})
    assert inst.status == "success"
    assert inst.data is not None
    assert "session_file" not in inst.data


def test_result_data_with_session_file() -> None:
    """New workers returning session_file in data validate without error."""
    inst = JobResult.model_validate(
        {
            **_RESULT_BASE,
            "data": {
                "output": "done",
                "session_file": "/tmp/omp-sessions/sess-xyz987.json",
            },
        }
    )
    assert inst.status == "success"
    assert inst.data is not None
    assert inst.data["session_file"] == "/tmp/omp-sessions/sess-xyz987.json"
