"""Tests for roxabi_contracts.cli.models — RED phase (T5).

Import will fail until T6 ships the implementation. Tests are syntactically
valid and structurally complete per the cli/ domain spec (issue #941).

All five models extend ContractEnvelope (contract_version, trace_id,
issued_at) with extra="ignore" forward-compat.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

import pytest
from pydantic import ValidationError

from roxabi_contracts.cli.models import (
    CliChunkEvent,
    CliCmdPayload,
    CliControlAck,
    CliControlCmd,
    CliHeartbeat,
)

_ENVELOPE: dict[str, Any] = {
    "contract_version": "1",
    "trace_id": "tst-trace-cli",
    "issued_at": datetime(2026, 4, 26, tzinfo=timezone.utc),
}


# ---------------------------------------------------------------------------
# CliCmdPayload
# ---------------------------------------------------------------------------


def test_cli_cmd_payload_roundtrip() -> None:
    """Build a CliCmdPayload with all required fields, validate, check fields."""
    # Arrange
    payload = {
        **_ENVELOPE,
        "pool_id": "pool-1",
        "lyra_session_id": "sess-abc",
        "text": "summarise this",
        "model_cfg": {"model": "claude-opus-4-5"},
        "system_prompt": "You are a helpful assistant.",
    }

    # Act
    inst = CliCmdPayload.model_validate(payload)
    parsed = CliCmdPayload.model_validate_json(inst.model_dump_json())

    # Assert
    assert parsed == inst
    assert parsed.pool_id == "pool-1"
    assert parsed.lyra_session_id == "sess-abc"
    assert parsed.text == "summarise this"
    assert parsed.model_cfg == {"model": "claude-opus-4-5"}
    assert parsed.system_prompt == "You are a helpful assistant."


def test_cli_cmd_payload_defaults() -> None:
    """stream defaults to True; resume_session_id defaults to None."""
    # Arrange
    payload = {
        **_ENVELOPE,
        "pool_id": "pool-2",
        "lyra_session_id": "sess-xyz",
        "text": "hello",
        "model_cfg": {},
        "system_prompt": "sys",
    }

    # Act
    inst = CliCmdPayload.model_validate(payload)

    # Assert
    assert inst.stream is True
    assert inst.resume_session_id is None


def test_cli_cmd_payload_resume_session_id_accepted() -> None:
    """resume_session_id is accepted when provided."""
    # Arrange
    payload = {
        **_ENVELOPE,
        "pool_id": "pool-3",
        "lyra_session_id": "sess-res",
        "text": "continue",
        "model_cfg": {},
        "system_prompt": "sys",
        "resume_session_id": "prev-session-42",
    }

    # Act
    inst = CliCmdPayload.model_validate(payload)

    # Assert
    assert inst.resume_session_id == "prev-session-42"


def test_cli_cmd_payload_stream_false_accepted() -> None:
    """stream=False overrides the default."""
    # Arrange
    payload = {
        **_ENVELOPE,
        "pool_id": "pool-4",
        "lyra_session_id": "sess-s",
        "text": "batch",
        "model_cfg": {},
        "system_prompt": "sys",
        "stream": False,
    }

    # Act
    inst = CliCmdPayload.model_validate(payload)

    # Assert
    assert inst.stream is False


def test_cli_cmd_payload_extra_ignore() -> None:
    """Extra fields are silently ignored (ContractEnvelope model_config)."""
    # Arrange
    payload = {
        **_ENVELOPE,
        "pool_id": "pool-5",
        "lyra_session_id": "sess-extra",
        "text": "hi",
        "model_cfg": {},
        "system_prompt": "sys",
        "unknown_future_field": "should_be_ignored",
    }

    # Act — must not raise
    inst = CliCmdPayload.model_validate(payload)

    # Assert
    assert inst.pool_id == "pool-5"
    assert not hasattr(inst, "unknown_future_field")


# ---------------------------------------------------------------------------
# CliChunkEvent
# ---------------------------------------------------------------------------


def test_cli_chunk_event_text_type() -> None:
    """event_type='text' is a valid Literal."""
    # Arrange
    payload = {**_ENVELOPE, "pool_id": "pool-1", "event_type": "text", "text": "hello"}

    # Act
    inst = CliChunkEvent.model_validate(payload)

    # Assert
    assert inst.event_type == "text"
    assert inst.text == "hello"


def test_cli_chunk_event_result_type() -> None:
    """event_type='result' is a valid Literal."""
    # Arrange
    payload = {**_ENVELOPE, "pool_id": "pool-1", "event_type": "result", "done": True}

    # Act
    inst = CliChunkEvent.model_validate(payload)

    # Assert
    assert inst.event_type == "result"
    assert inst.done is True


def test_cli_chunk_event_invalid_type() -> None:
    """event_type='unknown' raises ValidationError."""
    # Arrange
    payload = {**_ENVELOPE, "pool_id": "pool-1", "event_type": "unknown"}

    # Act / Assert
    with pytest.raises(ValidationError):
        CliChunkEvent.model_validate(payload)


def test_cli_chunk_event_literals() -> None:
    """All five event_type literals are individually valid."""
    # Arrange
    valid_types = ["text", "tool_use", "session_id", "result", "error"]

    for event_type in valid_types:
        payload = {**_ENVELOPE, "pool_id": "pool-1", "event_type": event_type}

        # Act
        inst = CliChunkEvent.model_validate(payload)

        # Assert
        assert inst.event_type == event_type


def test_cli_chunk_event_defaults() -> None:
    """is_error defaults to False; done defaults to False; optional fields default to None."""  # noqa: E501
    # Arrange
    payload = {**_ENVELOPE, "pool_id": "pool-1", "event_type": "text"}

    # Act
    inst = CliChunkEvent.model_validate(payload)

    # Assert
    assert inst.is_error is False
    assert inst.done is False
    assert inst.text is None
    assert inst.session_id is None


def test_cli_chunk_event_session_id_type() -> None:
    """event_type='session_id' carries session_id field."""
    # Arrange
    payload = {
        **_ENVELOPE,
        "pool_id": "pool-1",
        "event_type": "session_id",
        "session_id": "sess-new-99",
    }

    # Act
    inst = CliChunkEvent.model_validate(payload)

    # Assert
    assert inst.session_id == "sess-new-99"


# ---------------------------------------------------------------------------
# CliControlCmd
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("op", ["reset", "resume_and_reset", "switch_cwd"])
def test_cli_control_cmd_ops(op: str) -> None:
    """op='reset'/'resume_and_reset'/'switch_cwd' are all valid Literals."""
    # Arrange
    payload = {**_ENVELOPE, "pool_id": "pool-ctrl", "op": op}

    # Act
    inst = CliControlCmd.model_validate(payload)

    # Assert
    assert inst.op == op
    assert inst.pool_id == "pool-ctrl"


def test_cli_control_cmd_invalid_op() -> None:
    """op='invalid' raises ValidationError."""
    # Arrange
    payload = {**_ENVELOPE, "pool_id": "pool-ctrl", "op": "invalid"}

    # Act / Assert
    with pytest.raises(ValidationError):
        CliControlCmd.model_validate(payload)


def test_cli_control_cmd_optional_fields() -> None:
    """session_id and cwd are None by default; accepted when provided."""
    # Arrange
    payload_minimal = {**_ENVELOPE, "pool_id": "pool-ctrl", "op": "reset"}
    payload_full = {
        **_ENVELOPE,
        "pool_id": "pool-ctrl",
        "op": "switch_cwd",
        "cwd": "/tmp/workspace",
        "session_id": "sess-42",
    }

    # Act
    inst_min = CliControlCmd.model_validate(payload_minimal)
    inst_full = CliControlCmd.model_validate(payload_full)

    # Assert
    assert inst_min.session_id is None
    assert inst_min.cwd is None
    assert inst_full.cwd == "/tmp/workspace"
    assert inst_full.session_id == "sess-42"


# ---------------------------------------------------------------------------
# CliControlAck
# ---------------------------------------------------------------------------


def test_cli_control_ack_fields() -> None:
    """ok=True/False accepted; resumed is optional."""
    # Arrange
    payload_ok = {**_ENVELOPE, "pool_id": "pool-ack", "ok": True}
    payload_nok = {**_ENVELOPE, "pool_id": "pool-ack", "ok": False, "resumed": False}
    payload_resumed = {**_ENVELOPE, "pool_id": "pool-ack", "ok": True, "resumed": True}

    # Act
    inst_ok = CliControlAck.model_validate(payload_ok)
    inst_nok = CliControlAck.model_validate(payload_nok)
    inst_resumed = CliControlAck.model_validate(payload_resumed)

    # Assert
    assert inst_ok.ok is True
    assert inst_ok.resumed is None
    assert inst_nok.ok is False
    assert inst_nok.resumed is False
    assert inst_resumed.resumed is True


def test_cli_control_ack_roundtrip() -> None:
    """CliControlAck round-trips cleanly through JSON."""
    # Arrange
    payload = {**_ENVELOPE, "pool_id": "pool-ack", "ok": True, "resumed": True}

    # Act
    inst = CliControlAck.model_validate(payload)
    parsed = CliControlAck.model_validate_json(inst.model_dump_json())

    # Assert
    assert parsed == inst


# ---------------------------------------------------------------------------
# CliHeartbeat
# ---------------------------------------------------------------------------


def test_cli_heartbeat_fields() -> None:
    """worker_id and pool_count are present and correctly typed."""
    # Arrange
    payload = {
        **_ENVELOPE,
        "worker_id": "worker-001",
        "pool_count": 3,
    }

    # Act
    inst = CliHeartbeat.model_validate(payload)

    # Assert
    assert inst.worker_id == "worker-001"
    assert inst.pool_count == 3


def test_cli_heartbeat_roundtrip() -> None:
    """CliHeartbeat round-trips cleanly through JSON."""
    # Arrange
    payload = {**_ENVELOPE, "worker_id": "worker-002", "pool_count": 0}

    # Act
    inst = CliHeartbeat.model_validate(payload)
    parsed = CliHeartbeat.model_validate_json(inst.model_dump_json())

    # Assert
    assert parsed == inst


def test_cli_heartbeat_pool_count_zero() -> None:
    """pool_count=0 is valid (idle worker)."""
    # Arrange
    payload = {**_ENVELOPE, "worker_id": "worker-idle", "pool_count": 0}

    # Act
    inst = CliHeartbeat.model_validate(payload)

    # Assert
    assert inst.pool_count == 0
