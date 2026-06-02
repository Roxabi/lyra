"""Tests for typing_publisher_shim internal behavior (#1377)."""

from __future__ import annotations

import asyncio
from unittest.mock import MagicMock, patch

import pytest

from factory.transport.typing_publisher import TypingPublisher
from factory.transport.work_scope import WorkScope
from factory.typing.listener import typing_publisher_shim


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
                    platform="telegram",
                    bot_id="main",
                    scope_id=1,
                    publisher=publisher,
                    method=_failing_method,
                    trace_id="trace-abc",
                )
                assert result is True
                # Wait for the task to complete and the done callback to fire
                await asyncio.sleep(0.05)

        mock_warning.assert_called_once()
        assert mock_warning.call_args.args[0] == "typing publisher shim failed: %s"
        assert isinstance(mock_warning.call_args.args[1], RuntimeError)
        assert str(mock_warning.call_args.args[1]) == "boom"

    @pytest.mark.asyncio
    async def test_task_cancelled_returns_early_no_exception(self):
        """Cancelled task → _on_done returns early without logging."""
        publisher = MagicMock(spec=TypingPublisher)

        async def _slow_method(_):
            await asyncio.sleep(10)

        with patch("factory.typing.listener.is_typing_enabled", return_value=True):
            with patch("factory.typing.listener.log.warning") as mock_warning:
                result = typing_publisher_shim(
                    platform="telegram",
                    bot_id="main",
                    scope_id=1,
                    publisher=publisher,
                    method=_slow_method,
                    trace_id="trace-abc",
                )
                assert result is True
                # Cancel the pending task(s) excluding the current test task.
                current = asyncio.current_task()
                pending = [t for t in asyncio.all_tasks() if t is not current]
                for task in pending:
                    task.cancel()
                await asyncio.sleep(0.05)

        mock_warning.assert_not_called()

    @pytest.mark.asyncio
    async def test_trace_id_none_falls_back_to_uuid4(self):
        """trace_id=None → WorkScope constructed with a uuid4().hex fallback."""
        publisher = MagicMock(spec=TypingPublisher)
        seen_scope: WorkScope | None = None

        async def _capture_method(scope):
            nonlocal seen_scope
            seen_scope = scope
            await asyncio.sleep(0)

        with patch("factory.typing.listener.is_typing_enabled", return_value=True):
            with patch("factory.typing.listener.uuid4") as mock_uuid:
                mock_uuid.return_value.hex = "deadbeef1234"
                result = typing_publisher_shim(
                    platform="telegram",
                    bot_id="main",
                    scope_id=1,
                    publisher=publisher,
                    method=_capture_method,
                    trace_id=None,
                )

        assert result is True
        # Wait for the task to complete so _capture_method runs
        await asyncio.sleep(0)
        assert seen_scope is not None
        assert seen_scope.trace_id == "deadbeef1234"  # pyright: ignore[reportUnreachable]
