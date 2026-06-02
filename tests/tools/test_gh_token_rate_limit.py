"""RED tests for RateLimiter — abuse-floor semantics (issue #1151, Slice S2).

Tests pin the post-refactor behaviour:
  - Default min_interval_s is 10.0 s (was 45.0)
  - wait() emits a WARNING log when it actually sleeps (remaining > 0)
  - wait() emits NO WARNING log when no sleep is needed (remaining <= 0)

Tests 1 and 2 FAIL against the current implementation.
Test 3 also FAILS because MIN_TOKEN_TTL_SECONDS == 300 (pinned via import).
"""

from __future__ import annotations

import logging
import unittest.mock as mock

from factory.tools.gh_token.rate_limit import RateLimiter

_RL_SLEEP = "factory.tools.gh_token.rate_limit.asyncio.sleep"


class FakeClock:
    """Monotonic fake clock for deterministic rate-limiter tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


# ── tests ─────────────────────────────────────────────────────────────────────


def test_default_min_interval_is_10_seconds() -> None:
    """RateLimiter() default min_interval_s must be 10.0 after the refactor.

    FAILS until T5 updates rate_limit.py (currently 45.0).
    """
    rl = RateLimiter()
    assert rl._min_interval == 10.0, (  # noqa: SLF001
        f"Expected default min_interval=10.0, got {rl._min_interval}"  # noqa: SLF001
    )


async def test_wait_logs_warning_when_actually_sleeps(caplog) -> None:
    """wait() emits a WARNING when remaining > 0 (abuse-floor stall is observable).

    FAILS until T5 adds the warning log to RateLimiter.wait() — currently
    wait() sleeps silently with no log output.
    """
    # Arrange — mark at t=0, then advance only 1s so remaining ≈ 9s
    clock = FakeClock(start=0.0)
    rl = RateLimiter(min_interval_s=10.0, clock=clock.now)
    rl.mark()  # records _last_release = 0.0
    clock.advance(1.0)  # now t=1; remaining ≈ 9s

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    # Act
    with mock.patch(_RL_SLEEP, side_effect=fake_sleep):
        with caplog.at_level(
            logging.WARNING, logger="factory.tools.gh_token.rate_limit"
        ):
            await rl.wait()

    # Assert — actually slept AND warning was emitted
    assert slept, "wait() must have slept (remaining > 0)"
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert warning_records, (
        "wait() must emit a WARNING log when it stalls (abuse-floor observable in logs)"
    )
    # Message should reference the stall / abuse floor
    msg = warning_records[0].getMessage().lower()
    assert any(kw in msg for kw in ("abuse", "stall", "floor", "sleep", "remaining")), (
        f"Warning message does not mention abuse/stall/floor: {msg!r}"
    )


async def test_wait_no_log_when_no_sleep_needed(caplog) -> None:
    """wait() emits NO WARNING when remaining <= 0 (fast path, no stall).

    Sanity counterpart to test_wait_logs_warning_when_actually_sleeps.

    Also FAILS until T5 sets the default min_interval to 10.0 — this test
    pins that default as a precondition.
    """
    # Precondition: default must be 10s (new behaviour)
    rl_default = RateLimiter()
    assert rl_default._min_interval == 10.0, (  # noqa: SLF001
        f"Expected default min_interval=10.0, got {rl_default._min_interval}"  # noqa: SLF001
    )

    # Arrange — clock advanced past the interval so remaining <= 0
    clock = FakeClock(start=0.0)
    rl = RateLimiter(min_interval_s=10.0, clock=clock.now)
    rl.mark()  # _last_release = 0.0
    clock.advance(15.0)  # 15s elapsed > 10s interval → remaining = -5s

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    # Act
    with mock.patch(_RL_SLEEP, side_effect=fake_sleep):
        with caplog.at_level(
            logging.WARNING, logger="factory.tools.gh_token.rate_limit"
        ):
            await rl.wait()

    # Assert — no sleep, no warning
    assert not slept, "wait() must NOT sleep when remaining <= 0"
    warning_records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert not warning_records, (
        f"wait() must NOT emit WARNING when no sleep needed; got: {warning_records}"
    )
