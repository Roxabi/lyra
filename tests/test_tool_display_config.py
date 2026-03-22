"""Unit tests for ToolDisplayConfig and _load_tool_display_config (issue #386)."""

from __future__ import annotations

import pytest

from lyra.bootstrap.config import _load_tool_display_config
from lyra.core.tool_display_config import ToolDisplayConfig

# ---------------------------------------------------------------------------
# ToolDisplayConfig.defaults()
# ---------------------------------------------------------------------------


class TestToolDisplayConfigDefaults:
    def test_returns_tool_display_config_instance(self) -> None:
        # Act
        cfg = ToolDisplayConfig.defaults()
        # Assert
        assert isinstance(cfg, ToolDisplayConfig)

    def test_numeric_defaults(self) -> None:
        # Arrange / Act
        cfg = ToolDisplayConfig.defaults()
        # Assert
        assert cfg.names_threshold == 3
        assert cfg.group_threshold == 3
        assert cfg.bash_max_len == 60
        assert cfg.throttle_ms == 2000

    def test_show_contains_all_nine_keys(self) -> None:
        # Arrange
        expected_keys = {
            "edit",
            "write",
            "bash",
            "web_fetch",
            "web_search",
            "agent",
            "read",
            "grep",
            "glob",
        }
        # Act
        cfg = ToolDisplayConfig.defaults()
        # Assert
        assert set(cfg.show.keys()) == expected_keys

    def test_show_defaults_true_for_visible_tools(self) -> None:
        # Act
        cfg = ToolDisplayConfig.defaults()
        # Assert
        for key in ("edit", "write", "bash", "web_fetch", "web_search", "agent"):
            assert cfg.show[key] is True, f"Expected show[{key!r}] to be True"

    def test_show_defaults_false_for_silent_tools(self) -> None:
        # Act
        cfg = ToolDisplayConfig.defaults()
        # Assert
        for key in ("read", "grep", "glob"):
            assert cfg.show[key] is False, f"Expected show[{key!r}] to be False"

    def test_defaults_is_frozen(self) -> None:
        # Arrange
        cfg = ToolDisplayConfig.defaults()
        # Act / Assert — frozen dataclass raises FrozenInstanceError on mutation
        with pytest.raises(Exception):
            cfg.names_threshold = 99  # type: ignore[misc]


# ---------------------------------------------------------------------------
# ToolDisplayConfig.from_dict()
# ---------------------------------------------------------------------------


class TestToolDisplayConfigFromDict:
    def test_empty_dict_returns_defaults(self) -> None:
        # Act
        cfg = ToolDisplayConfig.from_dict({})
        # Assert
        assert cfg == ToolDisplayConfig.defaults()

    def test_partial_overrides_keep_remaining_defaults(self) -> None:
        # Arrange
        data = {"names_threshold": 5}
        # Act
        cfg = ToolDisplayConfig.from_dict(data)
        # Assert
        assert cfg.names_threshold == 5
        assert cfg.group_threshold == 3  # default preserved
        assert cfg.bash_max_len == 60  # default preserved
        assert cfg.throttle_ms == 2000  # default preserved

    def test_all_numeric_keys_overridden(self) -> None:
        # Arrange
        data = {
            "names_threshold": 10,
            "group_threshold": 7,
            "bash_max_len": 120,
            "throttle_ms": 500,
        }
        # Act
        cfg = ToolDisplayConfig.from_dict(data)
        # Assert
        assert cfg.names_threshold == 10
        assert cfg.group_threshold == 7
        assert cfg.bash_max_len == 120
        assert cfg.throttle_ms == 500

    def test_show_override_merged_with_defaults(self) -> None:
        # Arrange — flip "read" to True, keep everything else
        data = {"show": {"read": True}}
        # Act
        cfg = ToolDisplayConfig.from_dict(data)
        # Assert
        assert cfg.show["read"] is True  # overridden
        assert cfg.show["grep"] is False  # default preserved
        assert cfg.show["edit"] is True  # default preserved

    def test_show_all_false_disables_all(self) -> None:
        # Arrange
        all_false = {
            k: False
            for k in (
                "edit",
                "write",
                "bash",
                "web_fetch",
                "web_search",
                "agent",
                "read",
                "grep",
                "glob",
            )
        }
        data = {"show": all_false}
        # Act
        cfg = ToolDisplayConfig.from_dict(data)
        # Assert
        assert all(not v for v in cfg.show.values())

    def test_unknown_keys_ignored(self) -> None:
        # Arrange
        data = {
            "names_threshold": 2,
            "unknown_key": "oops",
            "show": {"unknown_tool": True},
        }
        # Act — should not raise
        cfg = ToolDisplayConfig.from_dict(data)
        # Assert — known keys parsed, unknown tolerated
        assert cfg.names_threshold == 2
        assert "unknown_tool" in cfg.show  # merged into show dict as-is


# ---------------------------------------------------------------------------
# _load_tool_display_config()
# ---------------------------------------------------------------------------


class TestLoadToolDisplayConfig:
    def test_absent_section_returns_defaults(self) -> None:
        # Arrange — raw config with no [tool_display] key
        raw: dict = {}
        # Act
        cfg = _load_tool_display_config(raw)
        # Assert
        assert cfg == ToolDisplayConfig.defaults()

    def test_present_section_parsed(self) -> None:
        # Arrange
        raw = {
            "tool_display": {
                "names_threshold": 6,
                "throttle_ms": 1000,
            }
        }
        # Act
        cfg = _load_tool_display_config(raw)
        # Assert
        assert cfg.names_threshold == 6
        assert cfg.throttle_ms == 1000
        assert cfg.group_threshold == 3  # default preserved

    def test_show_subsection_parsed(self) -> None:
        # Arrange
        raw = {
            "tool_display": {
                "show": {"bash": False, "read": True},
            }
        }
        # Act
        cfg = _load_tool_display_config(raw)
        # Assert
        assert cfg.show["bash"] is False
        assert cfg.show["read"] is True
        assert cfg.show["edit"] is True  # default preserved

    def test_returns_tool_display_config_type(self) -> None:
        # Arrange / Act
        cfg = _load_tool_display_config({"tool_display": {}})
        # Assert
        assert isinstance(cfg, ToolDisplayConfig)

    def test_empty_tool_display_section_returns_defaults(self) -> None:
        # Arrange — section present but empty
        raw = {"tool_display": {}}
        # Act
        cfg = _load_tool_display_config(raw)
        # Assert
        assert cfg == ToolDisplayConfig.defaults()
