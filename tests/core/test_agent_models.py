"""Tests for AgentRow dataclass — effort + soul fields and from_db_row unpack."""

from __future__ import annotations

from factory.core.agent.agent_models import AgentRow


class TestAgentRowEffortField:
    def test_effort_defaults_to_none(self) -> None:
        row = AgentRow(name="test", backend="claude-cli", model="claude-opus-4-6")
        assert row.effort is None

    def test_effort_accepts_valid_values(self) -> None:
        for val in ("low", "medium", "high", "xhigh", "max"):
            row = AgentRow(name="test", backend="claude-cli", model="m", effort=val)
            assert row.effort == val

    def test_effort_none_stored_as_none(self) -> None:
        row = AgentRow(name="test", backend="claude-cli", model="m", effort=None)
        assert row.effort is None

    def test_from_db_row_27_columns_soul_and_effort(self) -> None:
        """from_db_row unpacks effort + soul columns (27 total)."""
        row_tuple = (
            "myagent",
            "claude-cli",
            "claude-opus",
            10,
            "[]",
            0,
            None,
            "[]",
            None,
            None,
            "db",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00+00:00",
            0,
            "[]",
            None,
            None,
            0,
            None,
            None,
            "en",
            None,
            None,
            "high",
            None,
            "sha256:abc",
            512,
        )
        row = AgentRow.from_db_row(row_tuple)
        assert row.name == "myagent"
        assert row.effort == "high"
        assert row.soul_document_blob_ref == "sha256:abc"
        assert row.soul_document_bytes == 512

    def test_from_db_row_effort_null(self) -> None:
        """from_db_row with NULL effort (None) produces effort=None."""
        row_tuple = (
            "myagent",
            "claude-cli",
            "claude-opus",
            0,
            "[]",
            0,
            None,
            "[]",
            None,
            None,
            "db",
            "2024-01-01T00:00:00+00:00",
            "2024-01-01T00:00:00+00:00",
            0,
            "[]",
            None,
            None,
            0,
            None,
            None,
            "en",
            None,
            None,
            None,
            None,
            None,
            None,
        )
        row = AgentRow.from_db_row(row_tuple)
        assert row.effort is None
