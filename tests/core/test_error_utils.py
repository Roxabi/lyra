"""Tests for lyra.core.error_utils.safe_error_response."""

from __future__ import annotations

import logging
from unittest.mock import MagicMock

from lyra.core.error_utils import safe_error_response
from lyra.core.message import GENERIC_ERROR_REPLY, Response


class TestSafeErrorResponse:
    def test_happy_path_returns_generic_response(self) -> None:
        """Returns a Response with GENERIC_ERROR_REPLY content."""
        logger = MagicMock(spec=logging.Logger)
        exc = ValueError("something bad")

        result = safe_error_response(exc, logger, context="test context")

        assert isinstance(result, Response)
        assert result.content == GENERIC_ERROR_REPLY

    def test_logger_receives_exception_call_with_context(self) -> None:
        """logger.exception() is called with a message containing the context prefix."""
        logger = MagicMock(spec=logging.Logger)
        exc = RuntimeError("network failure")

        safe_error_response(exc, logger, context="svc plugin")

        logger.exception.assert_called_once()
        args = logger.exception.call_args[0]
        # First arg is the format string, second is the context, third is the exc
        assert "svc plugin" in str(args)

    def test_empty_context_does_not_crash(self) -> None:
        """Empty context string is allowed and does not raise."""
        logger = MagicMock(spec=logging.Logger)
        exc = Exception("some error")

        result = safe_error_response(exc, logger, context="")

        assert isinstance(result, Response)
        assert result.content == GENERIC_ERROR_REPLY

    def test_context_appears_in_logged_message(self) -> None:
        """The context prefix appears in the log message arguments."""
        logger = MagicMock(spec=logging.Logger)
        exc = OSError("disk full")

        safe_error_response(exc, logger, context="my-context")

        logger.exception.assert_called_once()
        # logger.exception(format, context, exc) — check that context is an arg
        call_args = logger.exception.call_args
        # All positional args as a flat string for easy assertion
        all_args = str(call_args)
        assert "my-context" in all_args
