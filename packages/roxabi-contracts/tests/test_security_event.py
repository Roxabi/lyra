"""Unit tests for SecurityEvent schema validation.

Covers: valid construction, required-field enforcement, kind Literal guard,
ContractEnvelope field inheritance, and extra-field dropping.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts.audit import SecurityEvent
from roxabi_contracts.envelope import CONTRACT_VERSION

_ISSUED_AT = datetime(2026, 4, 26, tzinfo=timezone.utc)

_VALID_BASE: dict[str, Any] = {
    "contract_version": CONTRACT_VERSION,
    "trace_id": "test-trace-001",
    "issued_at": _ISSUED_AT,
    "kind": "cli.subprocess.spawned",
    "pool_id": "pool-abc",
    "agent_name": "lyra-agent",
    "skip_permissions": False,
    "tools_restricted": False,
    "tools_allowlist": [],
    "model": "claude-sonnet-4-6",
    "pid": 12345,
}


def test_security_event_valid_unrestricted() -> None:
    # Arrange
    data = {**_VALID_BASE, "tools_restricted": False, "tools_allowlist": []}
    # Act
    event = SecurityEvent.model_validate(data)
    # Assert
    assert event.tools_restricted is False
    assert event.tools_allowlist == []
    assert event.pool_id == "pool-abc"
    assert event.agent_name == "lyra-agent"
    assert event.skip_permissions is False
    assert event.model == "claude-sonnet-4-6"
    assert event.pid == 12345


def test_security_event_valid_restricted() -> None:
    # Arrange
    data = {
        **_VALID_BASE,
        "tools_restricted": True,
        "tools_allowlist": ["Read", "Bash"],
    }
    # Act
    event = SecurityEvent.model_validate(data)
    # Assert
    assert event.tools_restricted is True
    assert event.tools_allowlist == ["Read", "Bash"]


def test_security_event_missing_field_raises() -> None:
    # Arrange — omit pool_id
    data = {k: v for k, v in _VALID_BASE.items() if k != "pool_id"}
    # Act / Assert
    with pytest.raises(ValidationError) as exc_info:
        SecurityEvent.model_validate(data)
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("pool_id",) for e in errors)


def test_security_event_kind_literal() -> None:
    # Arrange — wrong kind value
    data = {**_VALID_BASE, "kind": "cli.subprocess.killed"}
    # Act / Assert
    with pytest.raises(ValidationError) as exc_info:
        SecurityEvent.model_validate(data)
    errors = exc_info.value.errors()
    assert any(e["loc"] == ("kind",) for e in errors)


def test_security_event_inherits_envelope_fields() -> None:
    # Arrange / Act
    event = SecurityEvent.model_validate(_VALID_BASE)
    # Assert — ContractEnvelope fields present
    assert event.contract_version == CONTRACT_VERSION
    assert event.trace_id == "test-trace-001"
    assert event.issued_at == _ISSUED_AT


def test_security_event_extra_ignore() -> None:
    # Arrange — inject unknown future field
    data = {**_VALID_BASE, "future_v2_field": "unexpected", "nested": {"a": 1}}
    # Act
    event = SecurityEvent.model_validate(data)
    dumped = event.model_dump()
    # Assert — unknown fields silently dropped
    assert "future_v2_field" not in dumped
    assert "nested" not in dumped
