"""Daemon entrypoint for the factory-gh token-mint helper.

Runs the dispenser (Unix-socket server) in a single asyncio event loop.
Token refresh is lazy — the dispenser mints on demand when the cached token
TTL drops below 15 min. There is no background refresh loop.

Designed to be launched as the main process of a sidecar Quadlet container
(or via `python -m factory.tools.gh_token.daemon`) running as uid 1501 —
separate from Claude's uid 1500.

Required env vars (fail-fast on missing):
    FACTORY_GH_APP_ID            — GitHub App ID, numeric string
    FACTORY_GH_INSTALLATION_ID   — Installation ID, numeric string
    FACTORY_GH_PEM_PATH          — Path to the App's RSA private key (PEM)

Optional env vars:
    FACTORY_GH_DISPENSER_SOCK    — defaults to /run/factory-gh-token/dispenser.sock
    FACTORY_GH_CACHE_PATH        — defaults to /run/factory-gh-token/token.json
    FACTORY_GH_RATE_LIMIT_S      — defaults to 10 (abuse-floor interval in seconds)

Exit codes:
    0  — clean shutdown (SIGTERM / SIGINT)
    2  — config error (missing/invalid env)
"""

from __future__ import annotations

import asyncio
import logging
import os
import signal
import sys
from dataclasses import dataclass
from pathlib import Path

import httpx

from factory.tools.gh_token.dispenser import Dispenser
from factory.tools.gh_token.helper import JWTSigner, TokenCache
from factory.tools.gh_token.rate_limit import RateLimiter

log = logging.getLogger(__name__)


_DEFAULT_SOCK = "/run/factory-gh-token/dispenser.sock"
_DEFAULT_CACHE = "/run/factory-gh-token/token.json"


class DaemonConfigError(Exception):
    """Raised when required env vars are missing or invalid."""


@dataclass(frozen=True, slots=True)
class DaemonConfig:
    app_id: str
    install_id: str
    pem_path: Path
    sock_path: Path
    cache_path: Path
    rate_limit_s: float


def _load_config() -> DaemonConfig:
    """Read env vars, validate, return a frozen config. Raises DaemonConfigError."""
    app_id = os.environ.get("FACTORY_GH_APP_ID", "").strip()
    install_id = os.environ.get("FACTORY_GH_INSTALLATION_ID", "").strip()
    pem_path_raw = os.environ.get("FACTORY_GH_PEM_PATH", "").strip()

    missing = [
        name
        for name, val in (
            ("FACTORY_GH_APP_ID", app_id),
            ("FACTORY_GH_INSTALLATION_ID", install_id),
            ("FACTORY_GH_PEM_PATH", pem_path_raw),
        )
        if not val
    ]
    if missing:
        raise DaemonConfigError(f"missing required env vars: {', '.join(missing)}")

    if not app_id.isdigit():
        raise DaemonConfigError(f"FACTORY_GH_APP_ID must be numeric, got {app_id!r}")
    if not install_id.isdigit():
        raise DaemonConfigError(
            f"FACTORY_GH_INSTALLATION_ID must be numeric, got {install_id!r}"
        )

    pem_path = Path(pem_path_raw)
    if not pem_path.is_file():
        raise DaemonConfigError(f"PEM file not found: {pem_path_raw}")

    return DaemonConfig(
        app_id=app_id,
        install_id=install_id,
        pem_path=pem_path,
        sock_path=Path(os.environ.get("FACTORY_GH_DISPENSER_SOCK", _DEFAULT_SOCK)),
        cache_path=Path(os.environ.get("FACTORY_GH_CACHE_PATH", _DEFAULT_CACHE)),
        rate_limit_s=float(os.environ.get("FACTORY_GH_RATE_LIMIT_S", "10")),
    )


async def run_daemon(config: DaemonConfig) -> None:
    """Start the dispenser. Runs until cancelled.

    Token refresh is lazy — the dispenser mints on demand when the cached
    token TTL drops below 15 min. No background refresh task is started.
    """
    signer = JWTSigner(config.pem_path)
    cache = TokenCache(config.cache_path)
    rate_limiter = RateLimiter(min_interval_s=config.rate_limit_s)
    lock = asyncio.Lock()

    async with httpx.AsyncClient(
        timeout=httpx.Timeout(10.0),
        limits=httpx.Limits(max_keepalive_connections=0),
    ) as http:
        dispenser = Dispenser(
            cache=cache,
            signer=signer,
            http=http,
            app_id=config.app_id,
            install_id=config.install_id,
            lock=lock,
            rate_limiter=rate_limiter,
        )

        config.sock_path.parent.mkdir(parents=True, exist_ok=True)
        if config.sock_path.exists():
            config.sock_path.unlink()

        server = await dispenser.serve(config.sock_path)
        log.info(
            "factory-gh-helper daemon started — app_id=%s install_id=%s",
            config.app_id,
            config.install_id,
        )

        task: asyncio.Task[None] = asyncio.create_task(server.serve_forever())

        try:
            await asyncio.gather(task)
        except asyncio.CancelledError:
            log.info("factory-gh-helper daemon shutting down")
        finally:
            server.close()
            await server.wait_closed()
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)


def _install_signal_handlers(loop: asyncio.AbstractEventLoop) -> None:
    """Convert SIGTERM / SIGINT into a clean cancellation of all tasks."""

    def _shutdown() -> None:
        for task in asyncio.all_tasks(loop):
            task.cancel()

    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, _shutdown)


async def _amain() -> int:
    try:
        config = _load_config()
    except DaemonConfigError as exc:
        log.error("config error: %s", exc)
        return 2

    loop = asyncio.get_running_loop()
    _install_signal_handlers(loop)

    try:
        await run_daemon(config)
    except asyncio.CancelledError:
        pass
    return 0


def main() -> int:
    """Synchronous entrypoint for `python -m factory.tools.gh_token.daemon`."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
    )
    return asyncio.run(_amain())


if __name__ == "__main__":
    sys.exit(main())
