"""GitHub App token-mint helper — JWT signer, cache, and API exchange.

Runs as uid 1501 (lyra-gh) inside lyra-clipool. Token never reaches Claude's
subprocess env (uid 1500). Tmpfs parent /run/lyra-gh-token/ is 0700
helper-owned — mounted by Quadlet, NOT chmod'd here.

Unix socket dispenser (serve() on /run/lyra-gh-token/dispenser.sock) lives in
dispenser.py — it imports the primitives defined here (TokenCache, JWTSigner,
InstallationToken, MintError, mint, mint_capped, refresh_loop).

TODO(T4): MintError → publish as MintFailureEvent on NATS (roxabi-contracts gh/ schema)
"""

from __future__ import annotations

import base64
import json
import logging
import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPrivateKey

log = logging.getLogger(__name__)

# ── Section A: InstallationToken ─────────────────────────────────────────────


@dataclass(frozen=True, slots=True)
class InstallationToken:
    """Ephemeral GitHub installation access token with expiry tracking."""

    token: str
    expires_at: datetime  # must be UTC-aware

    def is_near_expiry(self, now: datetime, leeway_s: int = 300) -> bool:
        """Return True when less than *leeway_s* seconds remain before expiry."""
        remaining = (self.expires_at - now).total_seconds()
        return remaining < leeway_s


# ── Section B: MintError ─────────────────────────────────────────────────────


class MintError(Exception):
    """Raised when a GitHub installation token cannot be minted.

    Carries structured context for the caller and for T4's failure-event path.
    """

    def __init__(
        self,
        reason: str,
        http_status: int | None = None,
        retries: int = 0,
    ) -> None:
        super().__init__(reason)
        self.reason = reason
        self.http_status = http_status
        self.retries = retries


# ── Section C: JWTSigner ─────────────────────────────────────────────────────


def _b64url(data: bytes) -> str:
    """Base64url-encode *data* with no padding."""
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode("ascii")


class JWTSigner:
    """RS256 JWT signer backed by a PEM RSA private key.

    Loads and caches the key at construction time. Caller is responsible for
    ensuring the PEM file is readable (mode 0400, helper-owned).
    """

    def __init__(self, pem_path: Path) -> None:
        raw = pem_path.read_bytes()
        key = serialization.load_pem_private_key(raw, password=None)
        if not isinstance(key, RSAPrivateKey):
            raise ValueError(
                f"Expected RSA private key, got {type(key).__name__}: {pem_path}"
            )
        self._private_key: RSAPrivateKey = key

    def sign(self, app_id: str, *, now: datetime) -> str:
        """Produce a RS256 JWT valid for 9 minutes (GitHub max is 10 min).

        Includes 60s clock-skew back-date on *iat*.
        """
        ts = int(now.timestamp())
        header: dict[str, str] = {"alg": "RS256", "typ": "JWT"}
        claims: dict[str, Any] = {
            "iat": ts - 60,
            "exp": ts + 540,
            "iss": app_id,
        }

        header_b64 = _b64url(json.dumps(header, separators=(",", ":")).encode())
        payload_b64 = _b64url(json.dumps(claims, separators=(",", ":")).encode())
        signing_input = f"{header_b64}.{payload_b64}".encode()

        sig_bytes = self._private_key.sign(
            signing_input,
            padding.PKCS1v15(),
            hashes.SHA256(),
        )
        return f"{header_b64}.{payload_b64}.{_b64url(sig_bytes)}"


# ── Section D: TokenCache ─────────────────────────────────────────────────────


def _parse_expires_at(raw: str) -> datetime:
    """Parse ISO-8601 datetime with mandatory timezone component."""
    # Python 3.11+ fromisoformat handles Z; 3.12 enforces it too.
    normalized = raw.replace("Z", "+00:00")
    return datetime.fromisoformat(normalized)


class TokenCache:
    """Atomic JSON cache for a single InstallationToken on tmpfs.

    Parent directory is expected to be mode 0700, helper-owned — the caller
    (Quadlet Tmpfs= directive) sets that up. This class only writes the file.
    """

    def __init__(self, path: Path) -> None:
        self._path = path

    def read(self) -> InstallationToken | None:
        """Return cached token if present, parseable, and not yet expired.

        Any read error (missing, corrupt JSON, bad schema) returns None —
        treat as a cold cache. Never raises.
        """
        try:
            raw = self._path.read_text(encoding="utf-8")
            data: dict[str, Any] = json.loads(raw)
            token: str = data["token"]
            expires_at = _parse_expires_at(data["expires_at"])
            # Ensure tz-aware
            if expires_at.tzinfo is None:
                expires_at = expires_at.replace(tzinfo=timezone.utc)
            now = datetime.now(tz=timezone.utc)
            if expires_at <= now:
                log.debug("TokenCache: cached token expired at %s", expires_at)
                return None
            return InstallationToken(token=token, expires_at=expires_at)
        except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch — cold-cache fallback: file may be missing, unreadable (PermissionError on misconfigured tmpfs), contain corrupt JSON, or have unexpected schema; all read errors are non-fatal — caller mints a fresh token
            log.debug("TokenCache read error (cold cache): %s", exc)
            return None

    def write(self, it: InstallationToken) -> None:
        """Atomically write *it* to the cache file with mode 0600.

        Write to .tmp → chmod(0o600) → os.replace() to ensure the final file
        is never visible with wrong permissions.
        """
        tmp = self._path.with_suffix(".tmp")
        payload = json.dumps(
            {
                "token": it.token,
                "expires_at": it.expires_at.isoformat(),
            },
            separators=(",", ":"),
        )
        tmp.write_text(payload, encoding="utf-8")
        tmp.chmod(0o600)
        os.replace(tmp, self._path)


# ── Section E: mint ───────────────────────────────────────────────────────────

_INSTALL_ID_RE = re.compile(r"^\d+$")
_GH_API_BASE = "https://api.github.com"


async def mint(
    app_id: str,
    install_id: str,
    *,
    signer: JWTSigner,
    http: httpx.AsyncClient,
) -> InstallationToken:
    """Mint a fresh GitHub installation access token.

    Args:
        app_id:     GitHub App ID (numeric string, e.g. "12345").
        install_id: Installation ID — must be purely numeric (path-traversal guard).
        signer:     Injected JWTSigner (allows test injection).
        http:       Injected httpx.AsyncClient (allows test injection).

    Raises:
        ValueError:  install_id contains non-digit characters.
        MintError:   Any non-201 HTTP response or network failure.
    """
    if not _INSTALL_ID_RE.match(install_id):
        raise ValueError(f"install_id must be purely numeric, got: {install_id!r}")

    now = datetime.now(tz=timezone.utc)
    jwt = signer.sign(app_id, now=now)

    url = f"{_GH_API_BASE}/app/installations/{install_id}/access_tokens"
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
        "User-Agent": "lyra-gh-token-helper/0.1",
    }

    try:
        resp = await http.post(url, headers=headers)
    except httpx.ConnectError as exc:
        raise MintError(
            reason=f"network error connecting to GitHub API: {exc}",
            http_status=None,
            retries=0,
        ) from exc
    except httpx.TransportError as exc:
        raise MintError(
            reason=f"network/transport error: {exc}",
            http_status=None,
            retries=0,
        ) from exc

    if resp.status_code != 201:
        raise MintError(
            reason=f"GitHub API returned http_status={resp.status_code}",
            http_status=resp.status_code,
            retries=0,
        )

    try:
        body: dict[str, Any] = resp.json()
        token: str = body["token"]
        expires_at = _parse_expires_at(body["expires_at"])
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=timezone.utc)
    except Exception as exc:  # noqa: BLE001 — DEBT:boundary-broad-catch — response parse: resp.json() raises JSONDecodeError, body["token"]/["expires_at"] raise KeyError (or TypeError if body is not a dict), _parse_expires_at raises ValueError; all wrapped into MintError
        raise MintError(
            reason=f"failed to parse GitHub API response: {exc}",
            http_status=resp.status_code,
            retries=0,
        ) from exc

    return InstallationToken(token=token, expires_at=expires_at)


# ── Section F: mint_capped ────────────────────────────────────────────────────
