"""Password hashing for control-plane users (stdlib scrypt — no extra dep)."""

from __future__ import annotations

import hashlib
import hmac
import secrets
from base64 import urlsafe_b64decode, urlsafe_b64encode

__all__ = ["hash_password", "hash_token", "verify_password"]

# scrypt parameters: interactive login (OWASP-ish, not memory-hard extreme)
_N = 2**14
_R = 8
_P = 1
_DKLEN = 32
_SALT_LEN = 16


def hash_token(raw: str) -> str:
    """SHA-256 hex digest for session/API-key/invite tokens at rest."""
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    """Return ``scrypt$N$r$p$salt$hash`` (urlsafe b64 for salt/hash)."""
    if not password:
        raise ValueError("password must be non-empty")
    salt = secrets.token_bytes(_SALT_LEN)
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_N,
        r=_R,
        p=_P,
        dklen=_DKLEN,
    )
    return (
        f"scrypt${_N}${_R}${_P}$"
        f"{urlsafe_b64encode(salt).decode('ascii')}$"
        f"{urlsafe_b64encode(dk).decode('ascii')}"
    )


def verify_password(password: str, encoded: str) -> bool:
    """Constant-time verify against ``hash_password`` output."""
    try:
        algo, n_s, r_s, p_s, salt_b64, hash_b64 = encoded.split("$", 5)
    except ValueError:
        return False
    if algo != "scrypt":
        return False
    try:
        n, r, p = int(n_s), int(r_s), int(p_s)
        salt = urlsafe_b64decode(salt_b64.encode("ascii"))
        expected = urlsafe_b64decode(hash_b64.encode("ascii"))
    except (ValueError, TypeError):
        return False
    dk = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=n,
        r=r,
        p=p,
        dklen=len(expected),
    )
    return hmac.compare_digest(dk, expected)
