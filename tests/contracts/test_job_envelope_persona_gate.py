"""Contract gate — JobEnvelope carries opaque system_prompt, never persona_json."""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from roxabi_contracts.jobs import JobEnvelope

_FORBIDDEN_PAYLOAD_KEYS = frozenset(
    {
        "persona_json",
        "soul_document_blob_ref",
        "soul_meta_json",
        "soul_sections",
    }
)

_GOLDEN_OMP_TURN: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "deadbeefcafebabe0123456789abcdef",
    "issued_at": datetime(2026, 6, 18, 12, 0, 0, tzinfo=timezone.utc),
    "job_id": "deadbeefcafebabe0123456789abcdef",
    "job_name": "omp",
    "payload": {
        "prompt": "ping",
        "model_cfg": {"backend": "omp-rpc", "model": "grok-4-fast"},
        "system_prompt": "sys",
        "pool_id": "pool-golden",
    },
    "reply_to": "_INBOX.deadbeef",
    "composite_depth": 0,
    "parent_job_id": None,
}


def test_golden_omp_envelope_excludes_persona_authoring_keys() -> None:
    env = JobEnvelope.model_validate(_GOLDEN_OMP_TURN)
    assert isinstance(env.payload["system_prompt"], str)
    assert _FORBIDDEN_PAYLOAD_KEYS.isdisjoint(env.payload.keys())


def test_system_prompt_is_str_not_structured_persona() -> None:
    env = JobEnvelope.model_validate(_GOLDEN_OMP_TURN)
    sp = env.payload["system_prompt"]
    assert isinstance(sp, str)
    assert not sp.strip().startswith("{")