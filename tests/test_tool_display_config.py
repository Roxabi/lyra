"""Unit tests for ToolDisplayConfig and _load_tool_display_config (issue #386)."""

from __future__ import annotations

from typing import Any, cast

import pytest
from pydantic import ValidationError

from lyra.bootstrap.factory.config import _load_tool_display_config
from lyra.core.messaging.tool_display_config import ToolDisplayConfig

# ---------------------------------------------------------------------------
# ToolDisplayConfig() — default construction
# ---------------------------------------------------------------------------


class TestToolDisplayConfigDefaults:
    def test_returns_tool_display_config_instance(self) -> None:
        # Act
        cfg = ToolDisplayConfig()
        # Assert
        assert isinstance(cfg, ToolDisplayConfig)

    def test_numeric_defaults(self) -> None:
        # Arrange / Act
        cfg = ToolDisplayConfig()
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
        cfg = ToolDisplayConfig()
        # Assert
        assert set(cfg.show.keys()) == expected_keys

    def test_show_defaults_true_for_visible_tools(self) -> None:
        # Act
        cfg = ToolDisplayConfig()
        # Assert
        for key in ("edit", "write", "bash", "web_fetch", "web_search", "agent"):
            assert cfg.show[key] is True, f"Expected show[{key!r}] to be True"

    def test_show_defaults_false_for_silent_tools(self) -> None:
        # Act
        cfg = ToolDisplayConfig()
        # Assert
        for key in ("read", "grep", "glob"):
            assert cfg.show[key] is False, f"Expected show[{key!r}] to be False"

    def test_defaults_is_frozen(self) -> None:
        # Arrange
        cfg = ToolDisplayConfig()
        # Act / Assert — Pydantic frozen model raises ValidationError on mutation
        with pytest.raises((ValidationError, TypeError)):
            setattr(cfg, "names_threshold", 99)

    def test_show_is_read_only(self) -> None:
        # Arrange — show is MappingProxyType; key mutation must raise TypeError
        cfg = ToolDisplayConfig()
        # Act / Assert
        with pytest.raises(TypeError):
            cast("dict[str, Any]", cfg.show)["bash"] = False

    def test_show_dict_instances_are_independent(self) -> None:
        # Arrange — each instance must get its own MappingProxyType (no shared ref)
        cfg1 = ToolDisplayConfig()
        cfg2 = ToolDisplayConfig()
        # Assert — contents are equal but not the same object
        assert cfg1.show is not cfg2.show


# ---------------------------------------------------------------------------
# ToolDisplayConfig.model_validate()
# ---------------------------------------------------------------------------


class TestToolDisplayConfigFromDict:
    def test_empty_dict_returns_defaults(self) -> None:
        # Act
        cfg = ToolDisplayConfig.model_validate({})
        # Assert
        assert cfg == ToolDisplayConfig()

    def test_partial_overrides_keep_remaining_defaults(self) -> None:
        # Arrange
        data = {"names_threshold": 5}
        # Act
        cfg = ToolDisplayConfig.model_validate(data)
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
        cfg = ToolDisplayConfig.model_validate(data)
        # Assert
        assert cfg.names_threshold == 10
        assert cfg.group_threshold == 7
        assert cfg.bash_max_len == 120
        assert cfg.throttle_ms == 500

    def test_show_override_merged_with_defaults(self) -> None:
        # Arrange — flip "read" to True, keep everything else
        data = {"show": {"read": True}}
        # Act
        cfg = ToolDisplayConfig.model_validate(data)
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
        cfg = ToolDisplayConfig.model_validate(data)
        # Assert
        assert all(not v for v in cfg.show.values())

    def test_unknown_keys_ignored(self) -> None:
        # Arrange
        data = {
            "names_threshold": 2,
            "unknown_key": "oops",
            "show": {"unknown_tool": True},
        }
        # Act — should not raise (extra="ignore" drops unknown top-level keys;
        # show validator merges show dict including unknown tool names)
        cfg = ToolDisplayConfig.model_validate(data)
        # Assert — known keys parsed, unknown tool merged into show dict
        assert cfg.names_threshold == 2
        assert "unknown_tool" in cfg.show  # merged into show dict as-is

    def test_invalid_string_for_numeric_field_raises_value_error(self) -> None:
        # Arrange — non-numeric value for a threshold field
        data = {"names_threshold": "fast"}
        # Act / Assert — Pydantic raises ValidationError on bad input
        with pytest.raises((ValueError, ValidationError)):
            ToolDisplayConfig.model_validate(data)

    def test_names_threshold_zero_raises_value_error(self) -> None:
        # Arrange
        data = {"names_threshold": 0}
        # Act / Assert
        with pytest.raises((ValueError, ValidationError), match="names_threshold"):
            ToolDisplayConfig.model_validate(data)

    def test_group_threshold_zero_raises_value_error(self) -> None:
        # Arrange
        data = {"group_threshold": 0}
        # Act / Assert
        with pytest.raises((ValueError, ValidationError), match="group_threshold"):
            ToolDisplayConfig.model_validate(data)

    def test_bash_max_len_zero_raises_value_error(self) -> None:
        # Arrange
        data = {"bash_max_len": 0}
        # Act / Assert
        with pytest.raises((ValueError, ValidationError), match="bash_max_len"):
            ToolDisplayConfig.model_validate(data)

    def test_throttle_ms_negative_raises_value_error(self) -> None:
        # Arrange
        data = {"throttle_ms": -1}
        # Act / Assert
        with pytest.raises((ValueError, ValidationError), match="throttle_ms"):
            ToolDisplayConfig.model_validate(data)

    def test_throttle_ms_zero_is_valid(self) -> None:
        # Arrange — throttle_ms=0 means "no throttle"; explicitly allowed (>= 0)
        data = {"throttle_ms": 0}
        # Act
        cfg = ToolDisplayConfig.model_validate(data)
        # Assert
        assert cfg.throttle_ms == 0


# ---------------------------------------------------------------------------
# _load_tool_display_config()
# ---------------------------------------------------------------------------


class TestLoadToolDisplayConfig:
    def test_absent_section_returns_defaults(self) -> None:
        # Arrange — raw config with no [tool_display] key
        raw: dict[str, object] = {}
        # Act
        cfg = _load_tool_display_config(raw)
        # Assert
        assert cfg == ToolDisplayConfig()

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
        assert cfg == ToolDisplayConfig()
