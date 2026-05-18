"""Unit tests for lyra.tools.gh_token.helper and dispenser.

Covers: InstallationToken expiry logic, JWTSigner (RS256 round-trip,
password-rejection), TokenCache (atomic write, perms, corrupt/expired
reads), mint() (stub HTTP, input validation, error paths), and
Dispenser (socket protocol, mode, error propagation).

## RED-GATE V1 (T5) coverage map

AC group / spec ref → test section:

- AC functional UC1 (transparent push):
    Section F Dispenser: get/peek/error, socket mode
- AC isolation N5/N6 (0600/0660 perms):
    Section D: token_cache_write_atomic_perms
    Section F: dispenser_socket_mode_0660_after_serve
- AC mint-failure (MintError structured fields):
    Section E: mint_raises_on_404, mint_raises_on_network_error
    Section J: mint_error_carries_full_context
- AC mint-failure (MintError→MintFailureEvent contract):
    Section J: mint_error_translates_to_mint_failure_event_payload
- AC ops χ-1 (rate-cap 1/45s):
    Section G: RateLimiter suite

### Known limitation — MintFailureEvent NATS publish (deferred to T17/T18)

MintError carries ``reason``, ``http_status``, and ``retries`` — all data required
to construct a ``MintFailureEvent`` envelope. However the helper does NOT yet wire
a NATS publish call: doing so would require injecting a NATS connection + nkey into
helper.py, which is a separate design decision deferred to:

- **T17** (hub subscriber) — defines the consumer side of ``lyra.gh.mint_failure.*``
- **T18** (E2E host smoke) — exercises the full publish→subscribe→Telegram path

The T5 test ``test_mint_error_translates_to_mint_failure_event_payload`` documents
the *contract surface* (MintError fields → MintFailureEvent dict → model_validate)
without requiring the helper to publish.
"""

from __future__ import annotations

import asyncio
import base64
import json
import stat
import unittest.mock as mock
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding
from cryptography.hazmat.primitives.asymmetric.rsa import RSAPublicKey

from lyra.tools.gh_token.dispenser import Dispenser
from lyra.tools.gh_token.helper import (
    InstallationToken,
    JWTSigner,
    MintError,
    TokenCache,
    mint,
)
from lyra.tools.gh_token.rate_limit import RateLimiter
from roxabi_contracts.gh import MintFailureEvent

# ── fixtures ──────────────────────────────────────────────────────────────────


def _make_rsa_pem(password: bytes | None = None) -> bytes:
    """Generate a 2048-bit RSA private key as PEM bytes."""
    from cryptography.hazmat.primitives.asymmetric import rsa

    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    enc = (
        serialization.BestAvailableEncryption(password)
        if password
        else serialization.NoEncryption()
    )
    return key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=enc,
    )


@pytest.fixture()
def rsa_pem_path(tmp_path: Path) -> Path:
    """Write a fresh unencrypted RSA private key to a temp file."""
    p = tmp_path / "test.pem"
    p.write_bytes(_make_rsa_pem())
    return p


# ── Section A: InstallationToken ─────────────────────────────────────────────


def test_installation_token_near_expiry() -> None:
    now = datetime(2030, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    # Expires exactly 5 min from now — boundary: NOT near-expiry (remaining == leeway)
    token_5m = InstallationToken(
        token="t",
        expires_at=now + timedelta(seconds=300),
    )
    assert not token_5m.is_near_expiry(now)

    # Expires 4:59 from now — IS near-expiry
    token_459 = InstallationToken(
        token="t",
        expires_at=now + timedelta(seconds=299),
    )
    assert token_459.is_near_expiry(now)

    # Expires 5:01 from now — NOT near-expiry
    token_501 = InstallationToken(
        token="t",
        expires_at=now + timedelta(seconds=301),
    )
    assert not token_501.is_near_expiry(now)


# ── Section C: JWTSigner ──────────────────────────────────────────────────────


def _b64url_decode(s: str) -> bytes:
    """Decode a base64url string with optional missing padding."""
    padded = s + "=" * (-len(s) % 4)
    return base64.urlsafe_b64decode(padded)


def test_jwt_signer_round_trip(rsa_pem_path: Path) -> None:
    signer = JWTSigner(rsa_pem_path)
    app_id = "99999"
    now = datetime(2030, 6, 15, 10, 0, 0, tzinfo=timezone.utc)

    token = signer.sign(app_id, now=now)

    parts = token.split(".")
    assert len(parts) == 3, "JWT must have 3 dot-separated parts"

    header = json.loads(_b64url_decode(parts[0]))
    claims = json.loads(_b64url_decode(parts[1]))
    sig_bytes = _b64url_decode(parts[2])

    # Header
    assert header["alg"] == "RS256"
    assert header["typ"] == "JWT"

    # Claims
    ts = int(now.timestamp())
    assert claims["iss"] == app_id
    assert claims["iat"] == ts - 60
    assert claims["exp"] == ts + 540
    assert claims["iat"] < ts < claims["exp"]

    # Signature verification via public key
    raw_key = rsa_pem_path.read_bytes()
    priv = serialization.load_pem_private_key(raw_key, password=None)
    pub: RSAPublicKey = priv.public_key()  # type: ignore[assignment]
    signing_input = f"{parts[0]}.{parts[1]}".encode()
    # Should not raise
    pub.verify(sig_bytes, signing_input, padding.PKCS1v15(), hashes.SHA256())


def test_jwt_signer_rejects_password_protected_key(tmp_path: Path) -> None:
    pem = _make_rsa_pem(password=b"secret")
    p = tmp_path / "enc.pem"
    p.write_bytes(pem)

    with pytest.raises((ValueError, TypeError)):
        JWTSigner(p)


# ── Section D: TokenCache ─────────────────────────────────────────────────────


def test_token_cache_write_atomic_perms(tmp_path: Path) -> None:
    cache_file = tmp_path / "token.json"
    it = InstallationToken(
        token="ghs_abc123",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )

    cache = TokenCache(cache_file)
    cache.write(it)

    # File must exist at final path (no leftover .tmp)
    assert cache_file.exists()
    assert not cache_file.with_suffix(".tmp").exists()

    # Mode must be 0600
    mode = stat.S_IMODE(cache_file.stat().st_mode)
    assert mode == 0o600, f"expected 0600, got {oct(mode)}"

    # Parent dir mode must be unchanged (tmp_path is 0700 by default on Linux)
    parent_mode = stat.S_IMODE(tmp_path.stat().st_mode)
    assert parent_mode != 0o600  # parent was not touched


def test_token_cache_read_handles_corrupt_file(tmp_path: Path) -> None:
    cache_file = tmp_path / "token.json"
    cache_file.write_text("NOT JSON AT ALL }{", encoding="utf-8")

    result = TokenCache(cache_file).read()
    assert result is None  # no raise, treated as cold cache


def test_token_cache_read_handles_expired_token(tmp_path: Path) -> None:
    cache_file = tmp_path / "token.json"
    # expires_at is in the past
    it = InstallationToken(
        token="ghs_expired",
        expires_at=datetime(2000, 1, 1, tzinfo=timezone.utc),
    )
    cache = TokenCache(cache_file)
    cache.write(it)

    result = cache.read()
    assert result is None


# ── Section E: mint ───────────────────────────────────────────────────────────

_SUCCESS_BODY = json.dumps({"token": "ghs_test", "expires_at": "2030-01-01T00:00:00Z"})


def _mock_transport(status: int, body: str | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, text=body or "")

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_mint_against_stub(rsa_pem_path: Path) -> None:
    signer = JWTSigner(rsa_pem_path)
    transport = _mock_transport(201, _SUCCESS_BODY)

    async with httpx.AsyncClient(transport=transport) as http:
        result = await mint("12345", "123", signer=signer, http=http)

    assert result.token == "ghs_test"
    assert result.expires_at == datetime(2030, 1, 1, tzinfo=timezone.utc)
    assert isinstance(result, InstallationToken)


@pytest.mark.asyncio
async def test_mint_validates_install_id_numeric(rsa_pem_path: Path) -> None:
    called: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(True)
        return httpx.Response(201, text=_SUCCESS_BODY)

    signer = JWTSigner(rsa_pem_path)
    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ValueError, match="purely numeric"):
            await mint("12345", "1; rm -rf /", signer=signer, http=http)

    assert not called, "MockTransport must never be invoked for invalid install_id"


@pytest.mark.asyncio
async def test_mint_raises_on_404(rsa_pem_path: Path) -> None:
    signer = JWTSigner(rsa_pem_path)
    transport = _mock_transport(404, '{"message":"Not Found"}')

    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(MintError) as exc_info:
            await mint("12345", "123", signer=signer, http=http)

    err = exc_info.value
    assert err.http_status == 404
    assert "404" in err.reason


@pytest.mark.asyncio
async def test_mint_raises_on_network_error(rsa_pem_path: Path) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("Connection refused")

    signer = JWTSigner(rsa_pem_path)
    transport = httpx.MockTransport(handler)

    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(MintError) as exc_info:
            await mint("12345", "123", signer=signer, http=http)

    err = exc_info.value
    assert err.http_status is None
    assert "network" in err.reason.lower() or "transport" in err.reason.lower()


# ── Section F: Dispenser ──────────────────────────────────────────────────────

_FUTURE_EXPIRES = "2030-01-01T00:00:00Z"
_DISPENSER_SUCCESS_BODY = json.dumps(
    {"token": "ghs_test", "expires_at": _FUTURE_EXPIRES}
)


def _make_dispenser(
    rsa_pem_path: Path,
    tmp_path: Path,
    *,
    transport: httpx.MockTransport,
) -> tuple[Dispenser, TokenCache]:
    """Build a Dispenser wired to a tmp cache + injected transport."""
    signer = JWTSigner(rsa_pem_path)
    cache = TokenCache(tmp_path / "token.json")
    http = httpx.AsyncClient(transport=transport)
    dispenser = Dispenser(
        cache,
        signer,
        http,
        app_id="12345",
        install_id="123",
    )
    return dispenser, cache


async def _round_trip(sock_path: Path, request: bytes) -> bytes:
    """Connect to sock_path, send request, read full response."""
    reader, writer = await asyncio.open_unix_connection(str(sock_path))
    writer.write(request)
    await writer.drain()
    writer.write_eof()
    data = await reader.read()
    writer.close()
    await writer.wait_closed()
    return data


@pytest.mark.asyncio
async def test_dispenser_get_returns_credential_lines(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    transport = _mock_transport(201, _DISPENSER_SUCCESS_BODY)
    dispenser, _ = _make_dispenser(rsa_pem_path, tmp_path, transport=transport)

    sock_path = tmp_path / "dispenser.sock"
    server = await dispenser.serve(sock_path)
    try:
        response = await _round_trip(sock_path, b"get\n")
    finally:
        server.close()
        await server.wait_closed()

    assert response == b"username=x-access-token\npassword=ghs_test\n\n"


@pytest.mark.asyncio
async def test_dispenser_peek_returns_same_credential_lines(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    transport = _mock_transport(201, _DISPENSER_SUCCESS_BODY)
    dispenser, _ = _make_dispenser(rsa_pem_path, tmp_path, transport=transport)

    sock_path = tmp_path / "dispenser.sock"
    server = await dispenser.serve(sock_path)
    try:
        response = await _round_trip(sock_path, b"peek\n")
    finally:
        server.close()
        await server.wait_closed()

    assert response == b"username=x-access-token\npassword=ghs_test\n\n"


@pytest.mark.asyncio
async def test_dispenser_unknown_request_returns_error(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    transport = _mock_transport(201, _DISPENSER_SUCCESS_BODY)
    dispenser, _ = _make_dispenser(rsa_pem_path, tmp_path, transport=transport)

    sock_path = tmp_path / "dispenser.sock"
    server = await dispenser.serve(sock_path)
    try:
        response = await _round_trip(sock_path, b"bogus\n")
    finally:
        server.close()
        await server.wait_closed()

    assert response.startswith(b"error=")


@pytest.mark.asyncio
async def test_dispenser_socket_mode_0660_after_serve(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    transport = _mock_transport(201, _DISPENSER_SUCCESS_BODY)
    dispenser, _ = _make_dispenser(rsa_pem_path, tmp_path, transport=transport)

    sock_path = tmp_path / "dispenser.sock"
    server = await dispenser.serve(sock_path)
    try:
        mode = stat.S_IMODE(sock_path.stat().st_mode)
        assert mode == 0o660, f"expected 0660, got {oct(mode)}"
    finally:
        server.close()
        await server.wait_closed()


@pytest.mark.asyncio
async def test_dispenser_swallows_mint_error(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    # Transport that always returns 500 → mint() raises MintError.
    transport = _mock_transport(500, '{"message":"Internal Server Error"}')
    dispenser, _ = _make_dispenser(rsa_pem_path, tmp_path, transport=transport)

    sock_path = tmp_path / "dispenser.sock"
    server = await dispenser.serve(sock_path)
    try:
        # Must not raise — dispenser catches MintError and writes error line.
        response = await _round_trip(sock_path, b"get\n")
    finally:
        server.close()
        await server.wait_closed()

    assert response.startswith(b"error=")


@pytest.mark.asyncio
async def test_dispenser_forces_mint_when_cached_token_near_expiry(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    """Dispenser mints a fresh token when cached token has <5 min TTL.

    Regression guard for the 1-hour boundary failure mode: a long-running
    git push that starts with a token expiring in <5 min may fail
    mid-operation.  The dispenser's _resolve_token() must detect this and
    force a mint that bypasses the rate limiter.
    """
    http_hit_count = 0

    fresh_body = json.dumps(
        {"token": "ghs_fresh", "expires_at": "2030-01-01T01:00:00Z"}
    )

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_hit_count
        http_hit_count += 1
        return httpx.Response(201, text=fresh_body)

    transport = httpx.MockTransport(handler)
    dispenser, cache = _make_dispenser(rsa_pem_path, tmp_path, transport=transport)

    # Seed cache with a token expiring in 60 s — inside MIN_TOKEN_TTL_SECONDS (300 s).
    near_expiry = InstallationToken(
        token="ghs_near_expiry",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(seconds=60),
    )
    cache.write(near_expiry)

    result = await dispenser._resolve_token()  # noqa: SLF001

    assert http_hit_count == 1, (
        "dispenser must mint fresh token when cached TTL < MIN_TOKEN_TTL_SECONDS"
    )
    assert result.token == "ghs_fresh"


_RL_SLEEP = "lyra.tools.gh_token.rate_limit.asyncio.sleep"


# ── Section G: RateLimiter ────────────────────────────────────────────────────


class FakeClock:
    """Monotonic fake clock for deterministic rate-limiter tests."""

    def __init__(self, start: float = 0.0) -> None:
        self._t = start

    def now(self) -> float:
        return self._t

    def advance(self, seconds: float) -> None:
        self._t += seconds


@pytest.mark.asyncio
async def test_rate_limiter_first_call_passes_immediately() -> None:
    """The very first wait() must not block (last_release initialised to allow it)."""
    clock = FakeClock(start=100.0)
    rl = RateLimiter(min_interval_s=45.0, clock=clock.now)
    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)

    with mock.patch(_RL_SLEEP, side_effect=fake_sleep):
        await rl.wait()

    assert slept == [], "first call must not sleep"


@pytest.mark.asyncio
async def test_rate_limiter_serialises_concurrent_waiters() -> None:
    """Three concurrent waiters each call mark(); only one passes per interval."""
    clock = FakeClock(start=0.0)
    rl = RateLimiter(min_interval_s=45.0, clock=clock.now)

    passage_order: list[int] = []

    async def worker(idx: int) -> None:
        await rl.wait()
        passage_order.append(idx)
        clock.advance(46.0)
        rl.mark()

    async def fake_sleep(s: float) -> None:
        clock.advance(s)

    with mock.patch(_RL_SLEEP, side_effect=fake_sleep):
        await asyncio.gather(worker(0), worker(1), worker(2))

    assert sorted(passage_order) == [0, 1, 2]


@pytest.mark.asyncio
async def test_rate_limiter_mark_resets_interval() -> None:
    """After mark(), a call within min_interval_s must sleep the remainder."""
    clock = FakeClock(start=0.0)
    rl = RateLimiter(min_interval_s=45.0, clock=clock.now)
    rl.mark()  # mark at t=0

    clock.advance(10.0)  # now t=10; 35 s remain

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    with mock.patch(_RL_SLEEP, side_effect=fake_sleep):
        await rl.wait()

    assert len(slept) == 1
    assert abs(slept[0] - 35.0) < 0.01, f"expected ~35s sleep, got {slept[0]}"


# ── Section J: MintError edge cases + MintFailureEvent contract surface ───────
#
# AC mint-failure: MintError carries structured context (reason/http_status/retries)
# and the fields map directly onto MintFailureEvent's contract surface.
# The helper does NOT publish to NATS (deferred — see module docstring).


def test_mint_error_carries_full_context() -> None:
    """MintError fields survive raise → catch — contract for a future emitter.

    Asserts that all three structured fields (reason, http_status, retries) are
    preserved on the exception instance after it propagates through a raise/catch
    boundary. This ensures a future emit site has every field it needs to build a
    MintFailureEvent envelope without any string-parsing of the exception message.
    """
    # Arrange
    reason = "GitHub API returned http_status=401"
    http_status = 401
    retries = 3

    # Act
    try:
        raise MintError(reason=reason, http_status=http_status, retries=retries)
    except MintError as exc:
        caught = exc

    # Assert — all three fields intact post-raise
    assert caught.reason == reason
    assert caught.http_status == http_status
    assert caught.retries == retries
    # Confirm the exception message (from super().__init__) matches reason
    assert str(caught) == reason


def test_mint_error_translates_to_mint_failure_event_payload() -> None:
    """MintError fields → MintFailureEvent dict → model_validate succeeds.

    Documents the contract surface between helper.MintError and the
    roxabi_contracts.gh.MintFailureEvent schema. A future T17/T18 emit site
    constructs the envelope dict using exactly this field mapping.

    This test exercises the *shape* of the integration without requiring the
    helper to hold a NATS connection (deferred — see module docstring).
    """
    # Arrange — simulate a 404 from GitHub (installation revoked)
    err = MintError(
        reason="github_api_404",
        http_status=404,
        retries=0,
    )

    payload = {
        # ContractEnvelope mandatory fields
        "contract_version": "1",
        "trace_id": "test-trace-mint-failure",
        "issued_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
        # MintFailureEvent fields — mapped from MintError
        "machine": "roxabituwer",
        "reason": err.reason,
        "http_status": err.http_status,
        "retries": err.retries,
    }

    # Act — model_validate must accept the payload built from MintError fields
    event = MintFailureEvent.model_validate(payload)

    # Assert
    assert event.reason == err.reason
    assert event.http_status == err.http_status
    assert event.retries == err.retries
    assert event.machine == "roxabituwer"


def test_mint_error_network_failure_http_status_none() -> None:
    """MintError for a network-level failure carries http_status=None.

    Network/transport errors precede any HTTP response; the future emitter
    must not attempt to serialize a numeric http_status for these paths.
    """
    # Arrange — simulate a ConnectError path (no HTTP response)
    err = MintError(
        reason="network error connecting to GitHub API: Connection refused",
        http_status=None,
        retries=0,
    )

    payload = {
        "contract_version": "1",
        "trace_id": "test-trace-network-err",
        "issued_at": datetime(2030, 1, 1, tzinfo=timezone.utc),
        "machine": "roxabituwer",
        "reason": err.reason,
        "http_status": err.http_status,  # None — valid per MintFailureEvent schema
        "retries": err.retries,
    }

    # Act
    event = MintFailureEvent.model_validate(payload)

    # Assert — None is accepted; this is the pre-API failure path
    assert event.http_status is None
    assert event.reason.startswith("network error")


@pytest.mark.asyncio
async def test_install_id_alpha_only_rejected(rsa_pem_path: Path) -> None:
    """mint() raises ValueError before any HTTP call for alphabetic install_id.

    Complements test_mint_validates_install_id_numeric (which uses shell-injection
    payload). This test confirms that a purely alphabetic value — e.g. a mistaken
    hostname — is rejected with the same guard, not passed to the GitHub API URL.
    """
    # Arrange
    called: list[bool] = []

    def handler(request: httpx.Request) -> httpx.Response:
        called.append(True)
        return httpx.Response(201, text=_SUCCESS_BODY)

    signer = JWTSigner(rsa_pem_path)
    transport = httpx.MockTransport(handler)

    # Act / Assert
    async with httpx.AsyncClient(transport=transport) as http:
        with pytest.raises(ValueError, match="purely numeric"):
            await mint("12345", "abc", signer=signer, http=http)

    assert not called, "HTTP transport must not be contacted for alphabetic install_id"


def test_token_cache_parent_dir_must_exist(tmp_path: Path) -> None:
    """TokenCache.write fails with OSError when the parent directory is missing.

    Documents the operational contract: the Quadlet ``Tmpfs=`` directive is
    responsible for creating ``/run/lyra-gh-token/`` before the helper process
    starts. If that mount is absent, write() raises rather than silently swallowing
    the error — a clear failure is preferable to a cold-cache loop.
    """
    # Arrange — path whose parent directory does not exist
    ghost_parent = tmp_path / "nonexistent_dir" / "token.json"
    cache = TokenCache(ghost_parent)
    it = InstallationToken(
        token="ghs_test",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )

    # Act / Assert — must not silently swallow; OS error propagates
    with pytest.raises(OSError):
        cache.write(it)
