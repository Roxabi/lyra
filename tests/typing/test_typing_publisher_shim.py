"""Tests for typing_publisher_shim internal behavior (#1377)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from factory.transport.typing_publisher import TypingPublisher
from factory.transport.work_scope import WorkScope
from factory.typing.listener import typing_publisher_shim

_SCOPE = WorkScope(
    platform="telegram",
    bot_id="main",
    scope_id=1,
    trace_id="trace-abc",
)


class TestOnDoneCallback:
    @pytest.mark.asyncio
    async def test_task_raises_exception_logs_warning(self):
        """Task that raises → _on_done logs a warning with the exception."""
        publisher = MagicMock(spec=TypingPublisher)

        async def _failing_method(_scope: WorkScope) -> None:
            raise RuntimeError("boom")

        with patch("factory.typing.listener.is_typing_enabled", return_value=True):
            with patch("factory.typing.listener.log.warning") as mock_warning:
                result = typing_publisher_shim(
                    _SCOPE,
                    publisher,
                    _failing_method,
                )
                assert result is True
                # Wait for the task to complete and the done callback to fire
                await asyncio.sleep(0.05)  # event-based

        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[0] == "typing publisher shim failed: %s"
        assert isinstance(mock_warning.call_args.args[1], RuntimeError)
        assert str(mock_warning.call_args.args[1]) == "boom"

    @pytest.mark.asyncio
    async def test_task_cancelled_returns_early_no_exception(self):
        """Cancelled task → _on_done returns early without logging."""
        publisher = MagicMock(spec=TypingPublisher)

        async def _slow_method(_):
            await asyncio.sleep(10)  # event-based

        with patch("factory.typing.listener.is_typing_enabled", return_value=True):
            with patch("factory.typing.listener.log.warning") as mock_warning:
                result = typing_publisher_shim(
                    _SCOPE,
                    publisher,
                    _slow_method,
                )
                assert result is True
                # Cancel the pending task(s) excluding the current test task.
                current = asyncio.current_task()
                pending = [t for t in asyncio.all_tasks() if t is not current]
                for task in pending:
                    task.cancel()
                await asyncio.sleep(0.05)  # event-based

        mock_warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_passes_work_scope_to_method(self):
        """WorkScope is forwarded unchanged to the publish method."""
        publisher = MagicMock(spec=TypingPublisher)
        seen_scope: WorkScope | None = None

        async def _capture_method(scope: WorkScope) -> None:
            nonlocal seen_scope
            seen_scope = scope
            await asyncio.sleep(0)  # event-based

        with patch("factory.typing.listener.is_typing_enabled", return_value=True):
            result = typing_publisher_shim(_SCOPE, publisher, _capture_method)

        assert result is True
        await asyncio.sleep(0)  # event-based
        assert seen_scope is _SCOPE