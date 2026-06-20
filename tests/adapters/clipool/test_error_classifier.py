"""Unit tests for clipool error_classifier (#1962)."""

from __future__ import annotations

import asyncio

import pytest

from factory.adapters.clipool.error_classifier import classify_exception

_SENSITIVE_TOKEN = "leak42-host:4222"


@pytest.mark.asyncio
async def test_classify_exception_worker_crash_does_not_leak_exception_str() -> None:
    """classify_exception(RuntimeError(sensitive)) → worker.crash with no leak."""
    leaky_exc = RuntimeError(f"unexpected failure talking to {_SENSITIVE_TOKEN}")

    we = classify_exception(leaky_exc)

    assert we.code == "worker.crash"
    assert we.retryable is True
    assert _SENSITIVE_TOKEN not in we.message
    assert "RuntimeError" in we.message


@pytest.mark.asyncio
async def test_classify_exception_session_lost_does_not_leak_exception_str() -> None:
    """asyncio.TimeoutError(sensitive) → cli.session_lost: no leak in message."""
    leaky_exc = asyncio.TimeoutError(f"connecting to {_SENSITIVE_TOKEN}")

    we = classify_exception(leaky_exc)

    assert we.code == "cli.session_lost"
    assert we.retryable is True
    assert _SENSITIVE_TOKEN not in we.message
    assert "TimeoutError" in we.message


@pytest.mark.asyncio
async def test_classify_exception_parse_does_not_leak_byte_sequence() -> None:
    """classify_exception(UnicodeDecodeError) → cli.parse, no byte sequence leak."""
    leaky_exc = UnicodeDecodeError(
        "utf-8", b"\xff\xfe" + _SENSITIVE_TOKEN.encode(), 0, 1, _SENSITIVE_TOKEN
    )

    we = classify_exception(leaky_exc)

    assert we.code == "cli.parse"
    assert we.retryable is False
    assert _SENSITIVE_TOKEN not in we.message
    assert "UnicodeDecodeError" in we.message
