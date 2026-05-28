# pyright: reportAttributeAccessIssue=false
"""Unit tests for OutboundAdapterBase.configure_tool_display() setter (#1468).

Covers:
1. Setter stores the given config on the instance as _tool_display_config.
2. Setter stores None as-is — no defaulting at write time.
3. An adapter that never had the setter called still defaults correctly in the
   read path (mirrors send_streaming's getattr fallback).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lyra.adapters.shared._base_outbound import OutboundAdapterBase
from lyra.core.messaging.tool_display_config import ToolDisplayConfig
from lyra.outbound.emitter import OutboundEmitter, PlatformCallbacks

# ---------------------------------------------------------------------------
# Minimal concrete subclass (OutboundAdapterBase is abstract)
# ---------------------------------------------------------------------------


class _MinimalAdapter(OutboundAdapterBase):
    """Minimal concrete subclass implementing all abstract methods."""

    async def send(self, original_msg, outbound) -> None:
        pass

    def _make_streaming_callbacks(self, original_msg, outbound) -> PlatformCallbacks:
        return PlatformCallbacks(
            send_placeholder=AsyncMock(return_value=(MagicMock(), 42)),
            edit_placeholder_text=AsyncMock(),
            send_trace_placeholder=AsyncMock(return_value=(object(), 42)),
            send_message=AsyncMock(return_value=99),
            send_fallback=AsyncMock(return_value=77),
            chunk_text=lambda text: [text],
            start_typing=MagicMock(),
            cancel_typing=MagicMock(),
            get_msg=MagicMock(side_effect=lambda key, fb: fb),
            placeholder_text="…",
        )

    def _make_emitter(self, original_msg, outbound) -> OutboundEmitter:
        return OutboundEmitter(
            self._make_streaming_callbacks(original_msg, outbound), outbound
        )

    def _start_typing(self, scope_id: int) -> None:
        pass

    def _cancel_typing(self, scope_id: int) -> None:
        pass


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConfigureToolDisplay:
    def test_setter_stores_config_on_instance(self) -> None:
        """configure_tool_display(cfg) stores cfg as _tool_display_config."""
        # Arrange
        adapter = _MinimalAdapter()
        cfg = ToolDisplayConfig(bash_max_len=200)

        # Act
        adapter.configure_tool_display(cfg)

        # Assert
        assert adapter._tool_display_config is cfg

    def test_setter_stores_none_as_is(self) -> None:
        """configure_tool_display(None) stores None — no defaulting at write time."""
        # Arrange
        adapter = _MinimalAdapter()

        # Act
        adapter.configure_tool_display(None)

        # Assert — None is stored verbatim; defaulting happens only in send_streaming
        assert adapter._tool_display_config is None

    def test_setter_overwrites_previous_value(self) -> None:
        """A second configure_tool_display() call replaces the previous config."""
        # Arrange
        adapter = _MinimalAdapter()
        first_cfg = ToolDisplayConfig(bash_max_len=100)
        second_cfg = ToolDisplayConfig(bash_max_len=200)

        # Act
        adapter.configure_tool_display(first_cfg)
        adapter.configure_tool_display(second_cfg)

        # Assert
        assert adapter._tool_display_config is second_cfg

    def test_read_path_defaults_when_setter_never_called(self) -> None:
        """Adapter with no setter call gets ToolDisplayConfig() defaults.

        Mirrors send_streaming's read path:
            getattr(self, "_tool_display_config", None) or ToolDisplayConfig()
        """
        # Arrange — fresh adapter, setter never called
        adapter = _MinimalAdapter()

        # Act — replicate send_streaming's read path
        resolved = getattr(adapter, "_tool_display_config", None) or ToolDisplayConfig()

        # Assert — falls back to default ToolDisplayConfig()
        assert isinstance(resolved, ToolDisplayConfig)
        assert resolved.bash_max_len == ToolDisplayConfig().bash_max_len

    def test_read_path_uses_stored_config_when_setter_was_called(self) -> None:
        """When configure_tool_display(cfg) was called, the read path returns cfg.

        Negative guard: if the setter is removed, _tool_display_config is missing
        and getattr returns None, so the read path falls back to ToolDisplayConfig()
        defaults — making the bash_max_len assertion below fail.
        """
        # Arrange
        adapter = _MinimalAdapter()
        cfg = ToolDisplayConfig(bash_max_len=300)
        adapter.configure_tool_display(cfg)

        # Act — replicate send_streaming's read path
        resolved = getattr(adapter, "_tool_display_config", None) or ToolDisplayConfig()

        # Assert — stored config is returned, not the default
        assert resolved is cfg
        assert resolved.bash_max_len == 300

    def test_read_path_falls_back_to_default_when_none_stored(self) -> None:
        """configure_tool_display(None) → read path falls back to ToolDisplayConfig().

        None stored as-is at write time, but the `or ToolDisplayConfig()` in
        send_streaming's read path then substitutes the default.
        """
        # Arrange
        adapter = _MinimalAdapter()
        adapter.configure_tool_display(None)

        # Act — replicate send_streaming's read path
        resolved = getattr(adapter, "_tool_display_config", None) or ToolDisplayConfig()

        # Assert — None triggers fallback to defaults
        assert isinstance(resolved, ToolDisplayConfig)
        assert resolved.bash_max_len == ToolDisplayConfig().bash_max_len
