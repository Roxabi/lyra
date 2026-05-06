"""Unit tests for lyra.tools.gh_token.helper and dispenser.

Covers: InstallationToken expiry logic, JWTSigner (RS256 round-trip,
password-rejection), TokenCache (atomic write, perms, corrupt/expired
reads), mint() (stub HTTP, input validation, error paths), and
Dispenser (socket protocol, mode, error propagation).
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
from lyra.tools.gh_token.refresh import mint_capped, refresh_loop

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

_SUCCESS_BODY = json.dumps(
    {"token": "ghs_test", "expires_at": "2030-01-01T00:00:00Z"}
)


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


_RL_SLEEP = "lyra.tools.gh_token.rate_limit.asyncio.sleep"
_REFRESH_SLEEP = "lyra.tools.gh_token.refresh.asyncio.sleep"


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


# ── Section H: mint_capped ────────────────────────────────────────────────────


def _make_mint_capped_args(
    rsa_pem_path: Path,
    tmp_path: Path,
    *,
    transport: httpx.MockTransport,
    min_interval_s: float = 0.0,
    clock: "FakeClock | None" = None,
) -> dict:
    """Return kwargs suitable for a mint_capped() call."""
    signer = JWTSigner(rsa_pem_path)
    cache = TokenCache(tmp_path / "token.json")
    http = httpx.AsyncClient(transport=transport)
    if clock is None:
        rl = RateLimiter(min_interval_s=min_interval_s)
    else:
        rl = RateLimiter(min_interval_s=min_interval_s, clock=clock.now)
    return {
        "app_id": "12345",
        "install_id": "123",
        "signer": signer,
        "http": http,
        "cache": cache,
        "lock": asyncio.Lock(),
        "rate_limiter": rl,
    }


@pytest.mark.asyncio
async def test_mint_capped_uses_cache_when_fresh(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    """mint_capped returns cached token without hitting httpx when cache is fresh."""
    http_hit_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_hit_count
        http_hit_count += 1
        return httpx.Response(201, text=_SUCCESS_BODY)

    transport = httpx.MockTransport(handler)
    args = _make_mint_capped_args(rsa_pem_path, tmp_path, transport=transport)

    fresh_token = InstallationToken(
        token="ghs_cached",
        expires_at=datetime(2030, 1, 1, tzinfo=timezone.utc),
    )
    args["cache"].write(fresh_token)

    result = await mint_capped(**args)

    assert result.token == "ghs_cached"
    assert http_hit_count == 0, "must not hit httpx when cache is fresh"


@pytest.mark.asyncio
async def test_mint_capped_locks_concurrent_callers(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    """5 concurrent mint_capped calls with empty cache must hit GitHub exactly once."""
    http_hit_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_hit_count
        http_hit_count += 1
        return httpx.Response(201, text=_SUCCESS_BODY)

    transport = httpx.MockTransport(handler)
    args = _make_mint_capped_args(rsa_pem_path, tmp_path, transport=transport)

    results = await asyncio.gather(*[mint_capped(**args) for _ in range(5)])

    assert http_hit_count == 1, f"expected 1 http hit, got {http_hit_count}"
    assert all(r.token == "ghs_test" for r in results)


@pytest.mark.asyncio
async def test_mint_capped_rate_caps_at_45s(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    """Rate limiter blocks second mint within 45s; 2 total http hits after advance."""
    http_hit_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_hit_count
        http_hit_count += 1
        return httpx.Response(201, text=_SUCCESS_BODY)

    transport = httpx.MockTransport(handler)
    clock = FakeClock(start=0.0)
    args = _make_mint_capped_args(
        rsa_pem_path, tmp_path, transport=transport, min_interval_s=45.0, clock=clock
    )

    # First call — cold cache.
    await mint_capped(**args)
    assert http_hit_count == 1

    # Write near-expiry token to force mint path (expires in 4 min → leeway=300s).
    from datetime import timedelta
    near = InstallationToken(
        token="ghs_near",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=4),
    )
    args["cache"].write(near)
    clock.advance(10.0)  # only 10s elapsed — rate limiter will sleep 35s

    slept: list[float] = []

    async def fake_sleep(s: float) -> None:
        slept.append(s)
        clock.advance(s)

    with mock.patch(_RL_SLEEP, side_effect=fake_sleep):
        await mint_capped(**args, leeway_s=300)

    assert http_hit_count == 2
    assert len(slept) >= 1, "rate limiter must have slept"


@pytest.mark.asyncio
async def test_refresh_and_caps(rsa_pem_path: Path, tmp_path: Path) -> None:
    """12 rapid mint_capped calls → 1 http hit; advance clock 46s → 2nd hit."""
    http_hit_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_hit_count
        http_hit_count += 1
        return httpx.Response(201, text=_SUCCESS_BODY)

    transport = httpx.MockTransport(handler)
    clock = FakeClock(start=0.0)
    args = _make_mint_capped_args(
        rsa_pem_path, tmp_path, transport=transport, min_interval_s=45.0, clock=clock
    )

    # 12 rapid concurrent calls — empty cache → exactly 1 http hit.
    results = await asyncio.gather(*[mint_capped(**args) for _ in range(12)])
    assert http_hit_count == 1, (
        f"expected 1 http hit after 12 rapid calls, got {http_hit_count}"
    )
    assert all(r.token == "ghs_test" for r in results)

    # Advance clock past the cap and write near-expiry token.
    clock.advance(46.0)
    from datetime import timedelta
    near = InstallationToken(
        token="ghs_near",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=4),
    )
    args["cache"].write(near)

    await mint_capped(**args, leeway_s=300)
    assert http_hit_count == 2, (
        f"expected 2 total http hits after clock advance, got {http_hit_count}"
    )


# ── Section I: refresh_loop ───────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_refresh_loop_warms_near_expiry_cache(
    rsa_pem_path: Path, tmp_path: Path
) -> None:
    """refresh_loop triggers a mint when cache is near expiry."""
    http_hit_count = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal http_hit_count
        http_hit_count += 1
        return httpx.Response(201, text=_SUCCESS_BODY)

    transport = httpx.MockTransport(handler)
    signer = JWTSigner(rsa_pem_path)
    cache = TokenCache(tmp_path / "token.json")
    http = httpx.AsyncClient(transport=transport)
    lock = asyncio.Lock()
    rl = RateLimiter(min_interval_s=0.0)

    # Near-expiry: expires in 4 minutes from now, leeway=600s → is_near_expiry True.
    from datetime import timedelta
    near = InstallationToken(
        token="ghs_near",
        expires_at=datetime.now(tz=timezone.utc) + timedelta(minutes=4),
    )
    cache.write(near)

    sleep_count = 0

    async def fake_sleep(s: float) -> None:
        nonlocal sleep_count
        sleep_count += 1
        if sleep_count >= 2:
            raise asyncio.CancelledError

    with mock.patch(_REFRESH_SLEEP, side_effect=fake_sleep):
        with pytest.raises(asyncio.CancelledError):
            await refresh_loop(
                app_id="12345",
                install_id="123",
                signer=signer,
                http=http,
                cache=cache,
                lock=lock,
                rate_limiter=rl,
                sleep_interval_s=60,
                near_expiry_leeway_s=600,
            )

    assert http_hit_count == 1, "refresh_loop must mint when cache is near expiry"
