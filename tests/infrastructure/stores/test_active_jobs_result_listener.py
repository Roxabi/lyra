"""Unit tests for ResultCloseListener (#1795).

AsyncMock nc/coordinator throughout — no NATS server, no sleeps.  _handle is
exercised directly with a stub Msg; the payload is never inspected by the
listener, so tests only vary the subject.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import nats.errors
import pytest

from factory.infrastructure.stores.jobs.active_jobs_result_listener import (
    ResultCloseListener,
    _extract_job_id,
)
from roxabi_contracts.jobs.subjects import JOB_RESULT_WILDCARD

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _msg(subject: str, data: bytes = b"{}") -> MagicMock:
    msg = MagicMock()
    msg.subject = subject
    msg.data = data
    return msg


@pytest.fixture()
def coordinator() -> AsyncMock:
    mock = AsyncMock()
    mock.close = AsyncMock(return_value=None)
    return mock


@pytest.fixture()
def nc() -> AsyncMock:
    return AsyncMock()


@pytest.fixture()
def listener(nc: AsyncMock, coordinator: AsyncMock) -> ResultCloseListener:
    return ResultCloseListener(nc, coordinator)


# ---------------------------------------------------------------------------
# start() / stop()
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_start_subscribes_result_wildcard(
    nc: AsyncMock, listener: ResultCloseListener
) -> None:
    await listener.start()
    nc.subscribe.assert_called_once_with(JOB_RESULT_WILDCARD, cb=listener._handle)


@pytest.mark.asyncio()
async def test_stop_unsubscribes(nc: AsyncMock, listener: ResultCloseListener) -> None:
    await listener.start()
    sub = nc.subscribe.return_value

    await listener.stop()

    sub.unsubscribe.assert_awaited_once()


@pytest.mark.asyncio()
async def test_stop_without_start_is_noop(listener: ResultCloseListener) -> None:
    await listener.stop()  # must not raise


@pytest.mark.asyncio()
async def test_stop_twice_unsubscribes_once(
    nc: AsyncMock, listener: ResultCloseListener
) -> None:
    await listener.start()
    sub = nc.subscribe.return_value

    await listener.stop()
    await listener.stop()

    sub.unsubscribe.assert_awaited_once()


# ---------------------------------------------------------------------------
# _handle() — close routing
# ---------------------------------------------------------------------------


@pytest.mark.asyncio()
async def test_result_closes_job_from_subject(
    coordinator: AsyncMock, listener: ResultCloseListener
) -> None:
    await listener._handle(_msg("factory.job.job-123.result"))
    coordinator.close.assert_awaited_once_with("job-123")


@pytest.mark.asyncio()
async def test_malformed_payload_still_closes(
    coordinator: AsyncMock, listener: ResultCloseListener
) -> None:
    """The subject is the close signal — payload bytes are never parsed."""
    await listener._handle(_msg("factory.job.job-9.result", data=b"\xff not json"))
    coordinator.close.assert_awaited_once_with("job-9")


@pytest.mark.asyncio()
async def test_unexpected_subject_skipped(
    coordinator: AsyncMock, listener: ResultCloseListener
) -> None:
    await listener._handle(_msg("factory.jobs.omp"))
    coordinator.close.assert_not_awaited()


@pytest.mark.asyncio()
async def test_close_nats_error_swallowed(
    coordinator: AsyncMock, listener: ResultCloseListener
) -> None:
    coordinator.close.side_effect = nats.errors.Error("transient")
    await listener._handle(_msg("factory.job.job-err.result"))  # must not raise


@pytest.mark.asyncio()
async def test_close_runtime_error_swallowed(
    coordinator: AsyncMock, listener: ResultCloseListener
) -> None:
    coordinator.close.side_effect = RuntimeError("store bug")
    await listener._handle(_msg("factory.job.job-err.result"))  # must not raise


# ---------------------------------------------------------------------------
# _extract_job_id()
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("subject", "expected"),
    [
        ("factory.job.abc123.result", "abc123"),
        ("factory.job.abc123.progress", None),
        ("factory.job..result", None),
        ("factory.jobs.abc123.result", None),
        ("factory.job.result", None),
        ("factory.job.a.b.result", None),
        ("", None),
    ],
)
def test_extract_job_id(subject: str, expected: str | None) -> None:
    assert _extract_job_id(subject) == expected
