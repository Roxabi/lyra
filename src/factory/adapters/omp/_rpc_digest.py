"""omp binary digest gate for RpcBridge."""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

_PINNED_SHA256 = "b877091c91ebdc8c8d907c4b62681895cd3ae049815858aea7b69ac1d53b7c7b"
_OMP_BIN = Path("/opt/omp/omp")
_ENV_REQUEST_TIMEOUT_KEY = "OMP_REQUEST_TIMEOUT"
_DEFAULT_REQUEST_TIMEOUT = 30.0


class DigestMismatchError(Exception):
    """Raised when the omp binary sha256 does not match the pinned value."""

    def __init__(self, actual: str, expected: str) -> None:
        super().__init__(f"digest mismatch: actual={actual} expected={expected}")
        self.actual = actual
        self.expected = expected


def read_request_timeout() -> float:
    """Read OMP_REQUEST_TIMEOUT from env; fall back to _DEFAULT_REQUEST_TIMEOUT."""
    raw = os.environ.get(_ENV_REQUEST_TIMEOUT_KEY)
    if raw is None:
        return _DEFAULT_REQUEST_TIMEOUT
    try:
        val = float(raw)
        return val if val > 0 else _DEFAULT_REQUEST_TIMEOUT
    except ValueError:
        return _DEFAULT_REQUEST_TIMEOUT


def verify_digest(omp_bin: Path) -> None:
    """Hash the omp binary and raise DigestMismatchError on mismatch."""
    hasher = hashlib.sha256()
    with omp_bin.open("rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            hasher.update(chunk)
    actual = hasher.hexdigest()
    if actual != _PINNED_SHA256:
        raise DigestMismatchError(actual=actual, expected=_PINNED_SHA256)
