"""Tests for Dispenser._resolve_token() — 15-min TTL invariant (issue #1151).

Pins the lazy-refresh-at-dispenser behaviour:
  - MIN_TOKEN_TTL_SECONDS == 900 (15 min)
  - Cold cache → mint() called once, result has TTL >= 15 min
  - Near-expiry (14 min TTL) → mint() called once, fresh token returned
  - Fresh cache (30 min TTL) → mint() NOT called, no lock acquisition
  - N=10 concurrent near-expiry callers → mint() called exactly once
"""

from __future__ import annotations

import asyncio
from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from lyra.tools.gh_token.dispenser import MIN_TOKEN_TTL_SECONDS, Dispenser
from lyra.tools.gh_token.helper import InstallationToken, MintError, TokenCache

# ── helpers ───────────────────────────────────────────────────────────────────

_MINT_PATH = "lyra.tools.gh_token.dispenser.mint"


def _make_token(ttl_minutes: int) -> InstallationToken:
    return InstallationToken(
        token="ghs_test",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )


def _make_fresh_token(ttl_minutes: int = 60) -> InstallationToken:
    """Return an InstallationToken that looks freshly minted (TTL ~60 min)."""
    return InstallationToken(
        token="ghs_fresh",
        expires_at=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )


def _make_dispenser(cache: TokenCache) -> Dispenser:
    """Build a Dispenser with a stub signer, http client, and the given cache."""
    signer = MagicMock()
    http = MagicMock()
    return Dispenser(
        cache,
        signer,
        http,
        app_id="12345",
        install_id="123",
    )


# ── constant guard ────────────────────────────────────────────────────────────


def test_min_token_ttl_seconds_is_900() -> None:
    """MIN_TOKEN_TTL_SECONDS must be 900 (15 min) after the refactor.

    FAILS until T2 updates dispenser.py (currently 300).
    """
    assert MIN_TOKEN_TTL_SECONDS == 900, (
        f"Expected MIN_TOKEN_TTL_SECONDS=900, got {MIN_TOKEN_TTL_SECONDS}"
    )


# ── dispenser tests ───────────────────────────────────────────────────────────


async def test_resolve_token_cold_cache_mints(tmp_path) -> None:
    """Empty cache → exactly one mint() call; returned token has TTL >= 15 min."""
    # Arrange — cache has no token
    cache = TokenCache(tmp_path / "token.json")
    dispenser = _make_dispenser(cache)

    fresh = _make_fresh_token(60)
    mock_mint = AsyncMock(return_value=fresh)

    # Act
    with patch(_MINT_PATH, mock_mint):
        result = await dispenser._resolve_token()  # noqa: SLF001

    # Assert
    mock_mint.assert_called_once()
    assert result.token == "ghs_fresh"
    now = datetime.now(timezone.utc)
    remaining = (result.expires_at - now).total_seconds()
    assert remaining >= MIN_TOKEN_TTL_SECONDS, (
        f"Returned token has only {remaining:.0f}s TTL, "
        f"expected >= {MIN_TOKEN_TTL_SECONDS}s"
    )


async def test_resolve_token_near_expiry_remints(tmp_path) -> None:
    """Cached token with TTL=14 min → mint() called once, fresh token returned.

    FAILS currently: the 14min token (840s TTL) is not near-expiry by the
    old 300s threshold, so _resolve_token() returns the stale cached token
    without calling mint() at all.
    """
    # Arrange — cache holds a token with 14 min remaining
    cache = TokenCache(tmp_path / "token.json")
    near_expiry = _make_token(14)
    cache.write(near_expiry)

    dispenser = _make_dispenser(cache)
    fresh = _make_fresh_token(60)
    mock_mint = AsyncMock(return_value=fresh)

    # Act
    with patch(_MINT_PATH, mock_mint):
        result = await dispenser._resolve_token()  # noqa: SLF001

    # Assert
    mock_mint.assert_called_once()
    assert result.token == "ghs_fresh"
    now = datetime.now(timezone.utc)
    remaining = (result.expires_at - now).total_seconds()
    assert remaining >= MIN_TOKEN_TTL_SECONDS, (
        f"Returned token has only {remaining:.0f}s TTL, "
        f"expected >= {MIN_TOKEN_TTL_SECONDS}s"
    )


async def test_resolve_token_fresh_returns_cached_no_mint(tmp_path) -> None:
    """Cached token with TTL=30 min → mint() NOT called, cached token returned.

    Also asserts no lock acquisition occurs (fast path skips the lock).

    FAILS currently because MIN_TOKEN_TTL_SECONDS is 300 (asserted below).
    Once the constant is corrected to 900, a 30-min token is above the
    threshold and the fast path applies — no lock, no mint.
    """
    # Arrange — cache holds a fresh token with 30 min remaining
    cache = TokenCache(tmp_path / "token.json")
    fresh = _make_token(30)
    cache.write(fresh)

    dispenser = _make_dispenser(cache)
    mock_mint = AsyncMock()

    # Spy on the lock to confirm it is NOT acquired on the fast path.
    original_lock = dispenser._lock  # noqa: SLF001
    lock_acquired_events: list[bool] = []

    class SpyLock:
        """Thin wrapper that records acquire() calls."""

        async def __aenter__(self):
            lock_acquired_events.append(True)
            return await original_lock.__aenter__()

        async def __aexit__(self, *args):
            return await original_lock.__aexit__(*args)

        def locked(self):
            return original_lock.locked()

    dispenser._lock = SpyLock()  # type: ignore[assignment]  # noqa: SLF001

    # Act
    with patch(_MINT_PATH, mock_mint):
        result = await dispenser._resolve_token()  # noqa: SLF001

    # Assert — mint not called, lock not acquired
    mock_mint.assert_not_called()
    assert lock_acquired_events == [], "Lock must NOT be acquired on the fast path"
    assert result.token == fresh.token


async def test_concurrent_near_expiry_mints_once(tmp_path) -> None:
    """N=10 concurrent _resolve_token() calls with TTL=14min → mint() called once."""
    # Arrange — cache holds a token with 14 min remaining
    cache = TokenCache(tmp_path / "token.json")
    near_expiry = _make_token(14)
    cache.write(near_expiry)

    dispenser = _make_dispenser(cache)

    fresh = _make_fresh_token(60)
    mint_call_count = 0

    async def counting_mint(*_args, **_kwargs) -> InstallationToken:
        del _args, _kwargs
        nonlocal mint_call_count
        mint_call_count += 1
        # Simulate brief async work; write to cache so subsequent lock waiters
        # see the fresh token and short-circuit.
        cache.write(fresh)
        return fresh

    # Act — 10 concurrent callers
    with patch(_MINT_PATH, side_effect=counting_mint):
        results = await asyncio.gather(
            *[dispenser._resolve_token() for _ in range(10)]  # noqa: SLF001
        )

    # Assert — exactly one GitHub API call regardless of concurrency
    assert mint_call_count == 1, (
        f"Expected mint() called exactly once, got {mint_call_count}"
    )
    assert all(r.token == "ghs_fresh" for r in results), (
        "All callers must receive the freshly minted token"
    )


async def test_resolve_token_propagates_mint_error(tmp_path) -> None:
    """Cold cache + MintError from mint() → MintError propagates (no fallback).

    Pins the spec's "no fallback to near-expired token on mint failure" decision:
    if mint() raises MintError the exception must propagate out of _resolve_token()
    so a future regression that swallows it would be caught here.
    """
    # Arrange — empty cache (cold)
    cache = TokenCache(tmp_path / "token.json")
    dispenser = _make_dispenser(cache)

    # Act + Assert
    with patch(_MINT_PATH, AsyncMock(side_effect=MintError("boom"))):
        with pytest.raises(MintError):
            await dispenser._resolve_token()  # noqa: SLF001
