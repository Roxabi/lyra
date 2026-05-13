"""Tests for AgentRow dataclass — effort field and from_db_row 25-column unpack."""

from __future__ import annotations

from lyra.core.agent.agent_models import AgentRow


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

    def test_from_db_row_25_columns_effort_at_end(self) -> None:
        """from_db_row must unpack the 25th column (effort) correctly."""
        row_tuple = (
            "myagent",  # name
            "claude-cli",  # backend
            "claude-opus",  # model
            10,  # max_turns
            "[]",  # tools_json
            0,  # show_intermediate
            None,  # smart_routing_json
            "[]",  # plugins_json
            None,  # memory_namespace
            None,  # cwd
            "db",  # source
            "2024-01-01T00:00:00+00:00",  # created_at
            "2024-01-01T00:00:00+00:00",  # updated_at
            0,  # skip_permissions
            "[]",  # permissions_json
            None,  # workspaces_json
            None,  # commands_json
            0,  # streaming
            None,  # persona_json
            None,  # voice_json
            "en",  # fallback_language
            None,  # patterns_json
            None,  # passthroughs_json
            1,  # show_tool_recap
            "high",  # effort (col 25)
        )
        row = AgentRow.from_db_row(row_tuple)
        assert row.name == "myagent"
        assert row.effort == "high"

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
            1,
            None,  # effort = NULL in DB
        )
        row = AgentRow.from_db_row(row_tuple)
        assert row.effort is None
