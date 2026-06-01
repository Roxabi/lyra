"""Typing shim tests for DiscordAdapter (T13, #1377).

Covers the conditional typing plane (pub/sub vs legacy ThrottleCapability):
1. LYRA_TYPING_ENABLED=true  -> _start_typing delegates to typing_publisher
2. LYRA_TYPING_ENABLED=true  -> _cancel_typing delegates to typing_publisher
3. LYRA_TYPING_ENABLED=false -> _start_typing calls legacy path
4. LYRA_TYPING_ENABLED=false -> _cancel_typing calls legacy path
5. No double calls (publisher is NOT touched when flag=false)
"""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import discord

from lyra.adapters.discord import DiscordAdapter

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_adapter(monkeypatch: Any, enabled: str) -> DiscordAdapter:
    """Minimal DiscordAdapter with env flag pre-seeded."""
    monkeypatch.setenv("LYRA_TYPING_ENABLED", enabled)
    adapter = DiscordAdapter(
        bot_id="main",
        inbound_bus=MagicMock(),
        intents=discord.Intents.none(),
    )
    return adapter


# ---------------------------------------------------------------------------
# Pub/sub path (LYRA_TYPING_ENABLED=true)
# ---------------------------------------------------------------------------


def test_dc_start_typing_enabled_delegates_to_publisher(monkeypatch: Any) -> None:
    """_start_typing with flag=true creates a task for publish_started."""
    adapter = _make_adapter(monkeypatch, "true")
    mock_publisher = AsyncMock()
    adapter._typing_publisher = mock_publisher

    with patch(
        "lyra.adapters.discord.adapter.TraceContext.get_trace_id",
        return_value="trace_dc_123",
    ):
        with patch("asyncio.create_task") as mock_create_task:
            adapter._start_typing(555)

    mock_publisher.publish_started.assert_called_once()
    scope = mock_publisher.publish_started.call_args.args[0]
    assert scope.platform == "discord"
    assert scope.bot_id == "main"
    assert scope.scope_id == 555
    assert scope.trace_id == "trace_dc_123"

    mock_create_task.assert_called_once()
    coro = mock_create_task.call_args.args[0]
    import asyncio
    assert asyncio.iscoroutine(coro)
    mock_publisher.publish_ended.assert_not_called()


def test_dc_cancel_typing_enabled_delegates_to_publisher(monkeypatch: Any) -> None:
    """_cancel_typing with flag=true creates a task for publish_ended."""
    adapter = _make_adapter(monkeypatch, "true")
    mock_publisher = AsyncMock()
    adapter._typing_publisher = mock_publisher

    with patch(
        "lyra.adapters.discord.adapter.TraceContext.get_trace_id",
        return_value="trace_dc_456",
    ):
        with patch("asyncio.create_task") as mock_create_task:
            adapter._cancel_typing(555)

    mock_publisher.publish_ended.assert_called_once()
    scope = mock_publisher.publish_ended.call_args.args[0]
    assert scope.platform == "discord"
    assert scope.bot_id == "main"
    assert scope.scope_id == 555
    assert scope.trace_id == "trace_dc_456"

    mock_create_task.assert_called_once()
    mock_publisher.publish_started.assert_not_called()


def test_dc_start_typing_enabled_no_publisher_is_noop(monkeypatch: Any) -> None:
    """_start_typing with flag=true but no _typing_publisher set is a no-op."""
    adapter = _make_adapter(monkeypatch, "true")
    # _typing_publisher is absent by default
    assert getattr(adapter, "_typing_publisher", None) is None

    with patch("asyncio.create_task") as mock_create_task:
        adapter._start_typing(555)

    mock_create_task.assert_not_called()


def test_dc_cancel_typing_enabled_no_publisher_is_noop(monkeypatch: Any) -> None:
    """_cancel_typing with flag=true but no _typing_publisher set is a no-op."""
    adapter = _make_adapter(monkeypatch, "true")
    assert getattr(adapter, "_typing_publisher", None) is None

    with patch("asyncio.create_task") as mock_create_task:
        adapter._cancel_typing(555)

    mock_create_task.assert_not_called()


# ---------------------------------------------------------------------------
# Legacy path (LYRA_TYPING_ENABLED=false)
# ---------------------------------------------------------------------------


def test_dc_start_typing_disabled_calls_legacy_path(monkeypatch: Any) -> None:
    """_start_typing with flag=false routes to TypingTaskManager.start."""
    import inspect

    adapter = _make_adapter(monkeypatch, "false")
    mock_publisher = AsyncMock()
    adapter._typing_publisher = mock_publisher

    with patch.object(adapter._typing, "start") as mock_start:
        adapter._start_typing(555)

    mock_start.assert_called_once()
    assert mock_start.call_args.args[0] == 555
    coro_factory = mock_start.call_args.args[1]
    coro = coro_factory()
    assert inspect.iscoroutine(coro)

    mock_publisher.publish_started.assert_not_called()
    mock_publisher.publish_ended.assert_not_called()


def test_dc_cancel_typing_disabled_calls_legacy_path(monkeypatch: Any) -> None:
    """_cancel_typing with flag=false routes to TypingTaskManager.cancel."""
    adapter = _make_adapter(monkeypatch, "false")
    mock_publisher = AsyncMock()
    adapter._typing_publisher = mock_publisher

    with patch.object(adapter._typing, "cancel") as mock_cancel:
        adapter._cancel_typing(555)

    mock_cancel.assert_called_once_with(555)

    mock_publisher.publish_started.assert_not_called()
    mock_publisher.publish_ended.assert_not_called()


# ---------------------------------------------------------------------------
# No-double-call guard
# ---------------------------------------------------------------------------


def test_dc_no_publisher_calls_when_flag_false(monkeypatch: Any) -> None:
    """When flag=false, the publisher is never called even if present."""
    adapter = _make_adapter(monkeypatch, "false")
    mock_publisher = AsyncMock()
    adapter._typing_publisher = mock_publisher

    with patch.object(adapter._typing, "start"):
        adapter._start_typing(111)
    with patch.object(adapter._typing, "cancel"):
        adapter._cancel_typing(111)

    mock_publisher.publish_started.assert_not_called()
    mock_publisher.publish_ended.assert_not_called()
