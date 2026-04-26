"""Tests for TrustedCallback and unwrap_callback (security fix #924)."""

from __future__ import annotations

import logging

import pytest

from lyra.core.messaging.callbacks import TrustedCallback, unwrap_callback

# ---------------------------------------------------------------------------
# TrustedCallback
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_trusted_callback_wraps_sync() -> None:
    called_with: list[object] = []

    def _sync(x: int) -> int:
        called_with.append(x)
        return x * 2

    cb: TrustedCallback[int] = TrustedCallback(_sync)
    result = await cb(7)

    assert result == 14
    assert called_with == [7]


@pytest.mark.asyncio
async def test_trusted_callback_wraps_async() -> None:
    called_with: list[object] = []

    async def _async(x: int) -> int:
        called_with.append(x)
        return x + 1

    cb: TrustedCallback[int] = TrustedCallback(_async)
    result = await cb(3)

    assert result == 4
    assert called_with == [3]


@pytest.mark.asyncio
async def test_trusted_callback_passes_kwargs() -> None:
    async def _fn(*, name: str) -> str:
        return f"hello {name}"

    cb: TrustedCallback[str] = TrustedCallback(_fn)
    result = await cb(name="world")

    assert result == "hello world"


def test_trusted_callback_stores_fn() -> None:
    def _fn() -> None:
        pass

    cb = TrustedCallback(_fn)
    assert cb.fn is _fn


# ---------------------------------------------------------------------------
# unwrap_callback
# ---------------------------------------------------------------------------


def test_unwrap_returns_trusted_callback() -> None:
    def _fn() -> None:
        pass

    cb = TrustedCallback(_fn)
    meta = {"_on_dispatched": cb}

    result = unwrap_callback(meta, "_on_dispatched")

    assert result is cb
    assert "_on_dispatched" in meta  # not popped


def test_unwrap_pop_removes_key() -> None:
    def _fn() -> None:
        pass

    cb = TrustedCallback(_fn)
    meta = {"_on_dispatched": cb}

    result = unwrap_callback(meta, "_on_dispatched", pop=True)

    assert result is cb
    assert "_on_dispatched" not in meta


def test_unwrap_returns_none_for_missing_key() -> None:
    meta: dict[str, object] = {}
    result = unwrap_callback(meta, "_on_dispatched")
    assert result is None


def test_unwrap_rejects_raw_callable_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    def _raw() -> None:
        pass

    meta: dict[str, object] = {"_on_dispatched": _raw}

    with caplog.at_level(logging.WARNING, logger="lyra.core.messaging.callbacks"):
        result = unwrap_callback(meta, "_on_dispatched")

    assert result is None
    assert any("Rejected untrusted callback" in r.message for r in caplog.records)
    assert any("_on_dispatched" in r.message for r in caplog.records)


def test_unwrap_rejects_lambda_and_logs(caplog: pytest.LogCaptureFixture) -> None:
    meta: dict[str, object] = {"_session_update_fn": lambda: None}

    with caplog.at_level(logging.WARNING, logger="lyra.core.messaging.callbacks"):
        result = unwrap_callback(meta, "_session_update_fn")

    assert result is None
    assert any("Rejected untrusted callback" in r.message for r in caplog.records)
    assert any("_session_update_fn" in r.message for r in caplog.records)


def test_unwrap_pop_missing_key_does_not_raise() -> None:
    meta: dict[str, object] = {}
    result = unwrap_callback(meta, "_on_dispatched", pop=True)
    assert result is None


@pytest.mark.asyncio
async def test_trusted_callback_propagates_sync_exception() -> None:
    def _raise() -> None:
        raise RuntimeError("boom")

    cb = TrustedCallback(_raise)
    with pytest.raises(RuntimeError, match="boom"):
        await cb()


@pytest.mark.asyncio
async def test_trusted_callback_propagates_async_exception() -> None:
    async def _raise() -> None:
        raise RuntimeError("async boom")

    cb = TrustedCallback(_raise)
    with pytest.raises(RuntimeError, match="async boom"):
        await cb()


def test_unwrap_rejects_non_callable_value_and_logs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    meta: dict[str, object] = {"_on_dispatched": 42}

    with caplog.at_level(logging.WARNING, logger="lyra.core.messaging.callbacks"):
        result = unwrap_callback(meta, "_on_dispatched")

    assert result is None
    assert any("Rejected untrusted callback" in r.message for r in caplog.records)


def test_unwrap_pop_removes_key_even_on_rejection(
    caplog: pytest.LogCaptureFixture,
) -> None:
    def _raw() -> None:
        pass

    meta: dict[str, object] = {"_on_dispatched": _raw}

    with caplog.at_level(logging.WARNING, logger="lyra.core.messaging.callbacks"):
        result = unwrap_callback(meta, "_on_dispatched", pop=True)

    assert result is None
    assert "_on_dispatched" not in meta
