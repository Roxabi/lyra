"""Mint orchestration: rate-capped single-flight mint + background refresh loop.

Wraps the helper-level ``mint()`` primitive with:
  - ``mint_capped`` — single-flight (asyncio.Lock) + per-installation
    RateLimiter cap so a burst of dispenser requests produces at most one
    GitHub /access_tokens hit per ``rate_limiter`` interval (default 45 s).
  - ``refresh_loop`` — background asyncio task that proactively mints when
    the cached token approaches expiry, so client requests never wait on a
    synchronous mint.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import httpx

from lyra.tools.gh_token.helper import (
    InstallationToken,
    JWTSigner,
    TokenCache,
    mint,
)
from lyra.tools.gh_token.rate_limit import RateLimiter

__all__ = ["mint_capped", "refresh_loop"]

log = logging.getLogger(__name__)


async def mint_capped(  # noqa: PLR0913 — POLICY:wiring
    app_id: str,
    install_id: str,
    *,
    signer: JWTSigner,
    http: httpx.AsyncClient,
    cache: TokenCache,
    lock: asyncio.Lock,
    rate_limiter: RateLimiter,
    leeway_s: int = 300,
) -> InstallationToken:
    """Return a valid token, minting at most once per ``rate_limiter`` interval.

    Single-flight pattern: the *lock* serialises concurrent callers so only
    one mint ever runs at a time. After acquiring the lock a second cache
    read is performed — if another waiter already minted, the fresh cached
    value is returned without a second GitHub API call.
    """
    now = datetime.now(tz=timezone.utc)
    cached = cache.read()
    if cached is not None and not cached.is_near_expiry(now, leeway_s):
        return cached

    async with lock:
        now = datetime.now(tz=timezone.utc)
        cached = cache.read()
        if cached is not None and not cached.is_near_expiry(now, leeway_s):
            return cached
        await rate_limiter.wait()
        it = await mint(app_id, install_id, signer=signer, http=http)
        cache.write(it)
        rate_limiter.mark()
        return it


async def refresh_loop(  # noqa: PLR0913 — POLICY:wiring
    *,
    app_id: str,
    install_id: str,
    signer: JWTSigner,
    http: httpx.AsyncClient,
    cache: TokenCache,
    lock: asyncio.Lock,
    rate_limiter: RateLimiter,
    sleep_interval_s: int = 60,
    near_expiry_leeway_s: int = 600,
) -> None:
    """Background task: proactively refresh the cached token when near expiry.

    Wakes every *sleep_interval_s* seconds.  If the cached token will expire
    within *near_expiry_leeway_s* seconds, calls ``mint_capped`` to warm the
    cache before any client request triggers a synchronous mint.

    Runs until cancelled (``asyncio.CancelledError`` is not caught).
    """
    while True:
        await asyncio.sleep(sleep_interval_s)
        now = datetime.now(tz=timezone.utc)
        cached = cache.read()
        if cached is None or cached.is_near_expiry(now, near_expiry_leeway_s):
            log.debug(
                "refresh_loop: token near expiry or absent — minting proactively"
            )
            try:
                await mint_capped(
                    app_id,
                    install_id,
                    signer=signer,
                    http=http,
                    cache=cache,
                    lock=lock,
                    rate_limiter=rate_limiter,
                    leeway_s=near_expiry_leeway_s,
                )
            except Exception as exc:  # noqa: BLE001 — POLICY:boundary — resilient loop: mint_capped raises MintError (which already wraps network errors, non-201 responses, and parse failures); loop must survive and retry on next interval
                log.warning("refresh_loop: mint failed: %s", exc)
