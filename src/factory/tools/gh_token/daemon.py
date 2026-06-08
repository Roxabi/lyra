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
import re
import signal
import socket
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

import httpx

from factory.tools.gh_token.dispenser import Dispenser
from factory.tools.gh_token.helper import JWTSigner, TokenCache
from factory.tools.gh_token.mint_failure_publisher import MintFailurePublisher
from factory.tools.gh_token.rate_limit import RateLimiter
from roxabi_nats import nats_connect

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

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
    signer: JWTSigner


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

    rate_limit_raw = os.environ.get("FACTORY_GH_RATE_LIMIT_S", "").strip()
    if not rate_limit_raw:
        rate_limit_s = 10.0
    else:
        try:
            rate_limit_s = float(rate_limit_raw)
        except ValueError:
            raise DaemonConfigError(
                "FACTORY_GH_RATE_LIMIT_S must be a numeric value,"
                f" got {rate_limit_raw!r}"
            )

    try:
        signer = JWTSigner(pem_path)
    except (ValueError, TypeError) as exc:
        raise DaemonConfigError(f"invalid PEM key at {pem_path}: {exc}") from exc

    return DaemonConfig(
        app_id=app_id,
        install_id=install_id,
        pem_path=pem_path,
        sock_path=Path(os.environ.get("FACTORY_GH_DISPENSER_SOCK", _DEFAULT_SOCK)),
        cache_path=Path(os.environ.get("FACTORY_GH_CACHE_PATH", _DEFAULT_CACHE)),
        rate_limit_s=rate_limit_s,
        signer=signer,
    )


def _safe_machine_name(raw: str) -> str:
    """Sanitize *raw* to a valid single NATS subject token.

    A valid token contains only ``[A-Za-z0-9_-]`` characters (no dots, spaces,
    wildcards, or ``>``) — the same charset the contract enforces on
    ``MintFailureEvent.machine`` via ``_validate_subject_segment`` (#1708). We
    inline a *sanitizer* here rather than reusing the contract *validator*
    because the daemon must never crash on a misconfigured ``FACTORY_MACHINE``:
    bad characters are replaced, not rejected.

    Invalid characters are replaced with ``_`` to preserve per-machine
    observability rather than collapsing all bad names to ``"unknown"`` (#26).
    """
    if re.fullmatch(r"[A-Za-z0-9_-]+", raw):
        return raw
    return re.sub(r"[^A-Za-z0-9_-]", "_", raw) or "unknown"


async def _connect_nats_publisher(
    nats_url: str,
) -> tuple["NATS | None", "MintFailurePublisher | None"]:
    """Best-effort NATS connect for mint-failure publishing.

    Returns ``(nc, publisher)``; both are ``None`` when NATS is unavailable
    or no URL is configured — the daemon must never crash due to NATS (#27).
    """
    if not nats_url:
        log.info("mint-failure publishing disabled — no NATS_URL")
        return None, None

    raw_machine = os.environ.get("FACTORY_MACHINE", socket.gethostname())
    machine = _safe_machine_name(raw_machine)
    if machine != raw_machine:
        log.warning(
            "FACTORY_MACHINE %r is not a valid NATS subject token"
            " — publishing mint-failures as %r",
            raw_machine,
            machine,
        )
    try:
        nc = await nats_connect(nats_url, identity_name="gh-helper")
        publisher = MintFailurePublisher(nc, machine)
        log.info(
            "mint-failure publishing enabled — subject factory.gh.mint_failure.%s",
            machine,
        )
        return nc, publisher
    except Exception as exc:  # noqa: BLE001 — must not crash daemon (#27: BindsTo → pod teardown)
        log.warning(
            "mint-failure publishing DISABLED — NATS connect to %r failed: %s"
            " (daemon continues, mint failures will NOT be published)",
            nats_url,
            exc,
        )
        return None, None


def _prepare_sock_path(config: DaemonConfig) -> None:
    """Create the socket directory and remove any stale socket file.

    Raises DaemonConfigError (mapped to exit code 2 by _amain) on
    filesystem errors so the daemon fails fast rather than leaving a
    broken socket in place.
    """
    try:
        config.sock_path.parent.mkdir(parents=True, exist_ok=True)
        if config.sock_path.exists():
            config.sock_path.unlink()
    except OSError as exc:
        raise DaemonConfigError(
            f"cannot prepare socket path {config.sock_path}: {exc}"
        ) from exc


async def run_daemon(config: DaemonConfig) -> None:
    """Start the dispenser. Runs until cancelled.

    Token refresh is lazy — the dispenser mints on demand when the cached
    token TTL drops below 15 min. No background refresh task is started.
    """
    cache = TokenCache(config.cache_path)
    rate_limiter = RateLimiter(min_interval_s=config.rate_limit_s)
    lock = asyncio.Lock()

    nats_url = os.environ.get("NATS_URL", "").strip()
    nc, publisher = await _connect_nats_publisher(nats_url)

    try:
        async with httpx.AsyncClient(
            timeout=httpx.Timeout(10.0),
            limits=httpx.Limits(max_keepalive_connections=0),
        ) as http:
            dispenser = Dispenser(
                cache=cache,
                signer=config.signer,
                http=http,
                app_id=config.app_id,
                install_id=config.install_id,
                lock=lock,
                rate_limiter=rate_limiter,
                publisher=publisher,
            )

            _prepare_sock_path(config)

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
                # task is already cancelled via CancelledError propagation (#51);
                # cancel() here is a no-op safety net for rare early-exit paths.
                if not task.done():  # noqa: SIM102 — guard against double-cancel noise (#52)
                    task.cancel()
                await asyncio.gather(task, return_exceptions=True)
    finally:
        if nc is not None:
            try:
                await asyncio.wait_for(nc.drain(), timeout=5.0)  # #9: bounded drain
            except asyncio.TimeoutError:
                log.warning("NATS drain timed out after 5 s — forcing close")
                try:
                    await nc.close()
                except Exception:  # noqa: BLE001 — best-effort close after drain timeout (#9)
                    pass
            except Exception:  # noqa: BLE001 — best-effort drain on shutdown (#9)
                pass


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
    except DaemonConfigError as exc:
        log.error("config error: %s", exc)
        return 2
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
