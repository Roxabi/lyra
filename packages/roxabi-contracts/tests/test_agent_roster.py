"""Tests for factory-state web agent roster wire contract."""

from __future__ import annotations

import json

import pytest
from pydantic import ValidationError

from roxabi_contracts.state.agent_roster import WEB_ROSTER_KEY, WebAgentRosterDocument


def test_web_roster_key() -> None:
    assert WEB_ROSTER_KEY == "roster.web"


def test_web_agent_roster_round_trip_json() -> None:
    doc = WebAgentRosterDocument(
        updated_at="2026-06-19T12:00:00Z",
        agents=["helper", "lyra_default"],
    )
    raw = doc.model_dump_json()
    restored = WebAgentRosterDocument.model_validate_json(raw)
    assert restored.schema_version == 1
    assert restored.agents == ["helper", "lyra_default"]


def test_web_agent_roster_rejects_config_fields() -> None:
    with pytest.raises(ValidationError):
        WebAgentRosterDocument.model_validate(
            {
                "schema_version": 1,
                "updated_at": "2026-06-19T12:00:00Z",
                "agents": ["lyra"],
                "backend": "claude",
            }
        )


def test_web_agent_roster_rejects_unknown_top_level_keys() -> None:
    with pytest.raises(ValidationError):
        WebAgentRosterDocument.model_validate(
            {
                "schema_version": 1,
                "updated_at": "2026-06-19T12:00:00Z",
                "agents": [],
                "tools_json": "[]",
            }
        )


def test_golden_web_roster_shape() -> None:
    payload = {
        "schema_version": 1,
        "updated_at": "2026-06-19T12:00:00Z",
        "agents": ["lyra_default"],
    }
    doc = WebAgentRosterDocument.model_validate(payload)
    assert json.loads(doc.model_dump_json(exclude_none=True)) == payload