"""Roundtrip tests for LLM-domain fixtures."""

from __future__ import annotations

from roxabi_contracts.llm import LlmHeartbeat
from roxabi_contracts.llm import fixtures as llm_fixtures


def test_llm_heartbeat_fixture_roundtrip() -> None:
    """Fixture round-trips: model_validate(sample) → model_dump() is a fixed point."""
    sample = llm_fixtures.sample_llm_heartbeat
    inst = LlmHeartbeat.model_validate(sample)
    dumped = inst.model_dump()
    assert dumped == sample
