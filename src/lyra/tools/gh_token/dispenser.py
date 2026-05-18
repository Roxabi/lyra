"""Unix socket dispenser for the lyra-gh token-mint helper.

Listens on /run/lyra-gh-token/dispenser.sock (mode 0660, group
lyra-tokenuser) and serves git-credential protocol responses. The dispenser
is the IPC seam between the helper-uid daemon (1501) and the Claude-uid
client (1500); they share the lyra-tokenuser group (gid 1502) so the
credential helper / lyra-gh shim can connect, but the cache file
/run/lyra-gh-token/token.json (mode 0600) remains unreadable to uid 1500.

Protocol (per https://git-scm.com/docs/git-credential#IOFMT):
  Client sends one line: "get\\n" or "peek\\n"
  Server responds:
    username=x-access-token\\n
    password=<token>\\n
    \\n
  Anything else → server writes "error=unsupported request\\n" and closes.
"""

from __future__ import annotations

import asyncio
import logging
import os
from datetime import datetime, timezone
from pathlib import Path

import httpx

from lyra.tools.gh_token.helper import (
    InstallationToken,
    JWTSigner,
    MintError,
    TokenCache,
    mint,
)
from lyra.tools.gh_token.rate_limit import RateLimiter

log = logging.getLogger(__name__)

_MAX_LINE_BYTES = 32

# Tokens with less than this many seconds remaining are considered near-expiry
# and will trigger a fresh mint. 15 min provides a safe buffer for long-running
# git operations well within the 1-hour token lifetime.
MIN_TOKEN_TTL_SECONDS = 900


class Dispenser:
    """Async Unix socket server that vends git-credential protocol responses.

    Constructed with injected cache/signer/http so tests can stub all I/O.

    A single ``asyncio.Lock`` and ``RateLimiter`` are held per instance,
    serialising concurrent mint operations and hard-capping GitHub API calls
    to ≤1 per 45 s (well inside GitHub's 1/min limit).
    """

    def __init__(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        cache: TokenCache,
        signer: JWTSigner,
        http: httpx.AsyncClient,
        *,
        app_id: str,
        install_id: str,
        lock: asyncio.Lock | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        self._cache = cache
        self._signer = signer
        self._http = http
        self._app_id = app_id
        self._install_id = install_id
        self._lock = lock if lock is not None else asyncio.Lock()
        self._rate_limiter = rate_limiter if rate_limiter is not None else RateLimiter()

    # ── public ────────────────────────────────────────────────────────────────

    async def serve(self, sock_path: Path) -> asyncio.AbstractServer:
        """Start a Unix socket server on *sock_path* and return it.

        The socket is created with the process umask (typically 0700/0600),
        then immediately chmod'd to 0660 so members of lyra-tokenuser (gid 1502)
        can connect. Socket ownership (uid:gid) is set by the Quadlet Tmpfs=
        mount options — not here.

        Caller owns the server lifecycle:
            server = await dispenser.serve(sock_path)
            async with server:
                await server.serve_forever()
        """
        server = await asyncio.start_unix_server(self._handle, str(sock_path))
        # chmod after bind — asyncio creates socket with process umask (typically 0700).
        os.chmod(sock_path, 0o660)
        log.info("Dispenser listening on %s (mode 0660)", sock_path)
        return server

    # ── internal ──────────────────────────────────────────────────────────────

    async def _resolve_token(self) -> InstallationToken:
        """Return a token with ≥ MIN_TOKEN_TTL_SECONDS remaining.

        Fast path (no lock): cached token with TTL ≥ 15 min → return directly.
        Slow path (under self._lock): cache miss or TTL < 15 min → re-check
        inside lock (thundering-herd guard) → abuse-floor wait → mint → cache → return.
        """
        now = datetime.now(tz=timezone.utc)
        cached = self._cache.read()
        if cached is not None and not cached.is_near_expiry(now, MIN_TOKEN_TTL_SECONDS):
            return cached

        async with self._lock:
            now = datetime.now(tz=timezone.utc)
            refreshed = self._cache.read()
            if refreshed is not None and not refreshed.is_near_expiry(
                now, MIN_TOKEN_TTL_SECONDS
            ):
                return refreshed
            await self._rate_limiter.wait()
            it = await mint(
                self._app_id,
                self._install_id,
                signer=self._signer,
                http=self._http,
            )
            self._cache.write(it)
            self._rate_limiter.mark()
            return it

    async def _handle(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
    ) -> None:
        """Handle one client connection: read 1 line, respond, close."""
        try:
            try:
                raw = await asyncio.wait_for(reader.readline(), timeout=2.0)
            except asyncio.TimeoutError:
                log.warning("Dispenser: client timed out reading request line")
                writer.write(b"error=client timeout\n")
                await writer.drain()
                return

            # Guard against oversized input.
            if len(raw) > _MAX_LINE_BYTES:
                log.warning(
                    "Dispenser: oversized request (%d bytes), rejecting", len(raw)
                )
                writer.write(b"error=request too long\n")
                await writer.drain()
                return

            cmd = raw.strip()

            if cmd in (b"get", b"peek"):
                # get and peek are semantically identical in V1.
                # peek is reserved for future debug/inspection use.
                try:
                    it = await self._resolve_token()
                except MintError as exc:
                    log.warning("Dispenser: mint failed: %s", exc)
                    writer.write(b"error=mint failed\n")
                    await writer.drain()
                    return

                response = (
                    f"username=x-access-token\npassword={it.token}\n\n"
                ).encode()
                writer.write(response)
                await writer.drain()

            else:
                log.debug("Dispenser: unsupported request %r", cmd)
                writer.write(b"error=unsupported request\n")
                await writer.drain()

        finally:
            writer.close()
            try:
                await writer.wait_closed()
            except Exception:  # noqa: BLE001 — DEBT:boundary-broad-catch — cleanup: writer.wait_closed() raises varied transport errors on peer disconnect; close must not propagate
                pass
