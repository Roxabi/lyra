"""Configuration dataclasses and constants for the Lyra pairing system.

Extracted from pairing.py (epic #293) — contains PairingError, PairingConfig,
SQL DDL, shared constants, and pure utility helpers.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, fields
from datetime import datetime, timezone


class PairingError(Exception):
    """Business-rule violation in the pairing system (e.g. max pending reached)."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_SAFE_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

_MAX_CODE_ATTEMPTS = 10

_CREATE_PAIRING_CODES = """
CREATE TABLE IF NOT EXISTS pairing_codes (
    id INTEGER PRIMARY KEY,
    code_hash TEXT NOT NULL UNIQUE,
    created_by TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    attempt_count INTEGER NOT NULL DEFAULT 0
)
"""


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class PairingConfig:
    """Immutable configuration for the pairing system."""

    alphabet: str = _SAFE_ALPHABET
    code_length: int = 8
    ttl_seconds: int = 3600
    max_pending: int = 3
    session_max_age_days: int = 30
    rate_limit_attempts: int = 5
    rate_limit_window: int = 300
    enabled: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> PairingConfig:
        """Create PairingConfig from a dict (e.g. TOML [pairing] section).

        Missing keys use field defaults.
        """
        known = {f.name for f in fields(cls)}
        filtered = {k: v for k, v in data.items() if k in known}
        return cls(**filtered)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)
