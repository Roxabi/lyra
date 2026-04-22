"""Tests for audit_matrix_inbox_drift — T11 (RED→GREEN via T8).

Covers:
  - clean fixtures (no findings)
  - bare _INBOX.> drift on a lyra-owned identity
  - lowercase _inbox.> drift
  - satellite exclusion
  - multiple drifts across identities
  - integration smoke: real acl-matrix.json returns []
"""

from pathlib import Path

import pytest

from lyra.cli_ops import _load_matrix
from lyra.ops_audit import audit_matrix_inbox_drift, format_drift_finding

# ---------------------------------------------------------------------------
# Test 1 — clean fixture: all lyra-owned identities use scoped subjects
# ---------------------------------------------------------------------------

_CLEAN_IDENTITIES: dict[str, dict] = {
    "hub": {
        "publish": ["lyra.outbound.telegram.>"],
        "subscribe": ["lyra.inbound.telegram.>", "_INBOX.hub.>"],
    },
    "telegram-adapter": {
        "publish": ["lyra.inbound.telegram.>"],
        "subscribe": ["lyra.outbound.telegram.>", "_INBOX.telegram-adapter.>"],
    },
    # satellite — must not be flagged even with bare _INBOX.>
    "voice-tts": {
        "publish": ["lyra.voice.tts.heartbeat", "_INBOX.>"],
        "subscribe": ["lyra.voice.tts.request.>"],
    },
}


def test_audit_clean_returns_empty() -> None:
    assert audit_matrix_inbox_drift(_CLEAN_IDENTITIES) == []


# ---------------------------------------------------------------------------
# Test 2 — drift: hub still has bare _INBOX.> on subscribe
# ---------------------------------------------------------------------------

_DRIFT_HUB: dict[str, dict] = {
    "hub": {
        "publish": ["lyra.outbound.telegram.>"],
        "subscribe": ["_INBOX.>", "lyra.>"],
    },
}


def test_audit_flags_hub_subscribe_drift() -> None:
    findings = audit_matrix_inbox_drift(_DRIFT_HUB)
    assert len(findings) == 1
    identity, grant, direction = findings[0]
    assert identity == "hub"
    assert grant == "_INBOX.>"
    assert direction == "subscribe"


# ---------------------------------------------------------------------------
# Test 3 — lowercase _inbox.> drift on tts-adapter
# ---------------------------------------------------------------------------

_DRIFT_LOWERCASE: dict[str, dict] = {
    "tts-adapter": {
        "publish": ["lyra.voice.tts.heartbeat"],
        "subscribe": ["_INBOX.tts-adapter.>", "_inbox.>"],
    },
}


def test_audit_flags_lowercase_inbox_drift() -> None:
    findings = audit_matrix_inbox_drift(_DRIFT_LOWERCASE)
    assert len(findings) == 1
    identity, grant, direction = findings[0]
    assert identity == "tts-adapter"
    assert grant == "_inbox.>"
    assert direction == "subscribe"


# ---------------------------------------------------------------------------
# Test 4 — satellite exclusion: voice-tts with bare _INBOX.> is NOT flagged
# ---------------------------------------------------------------------------

_SATELLITE_ONLY: dict[str, dict] = {
    "voice-tts": {
        "publish": ["_INBOX.>"],
        "subscribe": ["lyra.voice.tts.request.>"],
    },
}


def test_audit_satellite_excluded() -> None:
    assert audit_matrix_inbox_drift(_SATELLITE_ONLY) == []


# ---------------------------------------------------------------------------
# Test 5 — multiple drifts across identities
# ---------------------------------------------------------------------------

_MULTI_DRIFT: dict[str, dict] = {
    "hub": {
        "publish": ["_INBOX.>"],
        "subscribe": ["lyra.inbound.telegram.>"],
    },
    "discord-adapter": {
        "publish": ["lyra.inbound.discord.>"],
        "subscribe": ["_INBOX.>"],
    },
    # clean one in the middle — should not appear
    "telegram-adapter": {
        "publish": ["lyra.inbound.telegram.>"],
        "subscribe": ["_INBOX.telegram-adapter.>"],
    },
}


def test_audit_multiple_drifts() -> None:
    findings = audit_matrix_inbox_drift(_MULTI_DRIFT)
    assert len(findings) == 2
    identities_found = {(f[0], f[2]) for f in findings}
    assert ("hub", "publish") in identities_found
    assert ("discord-adapter", "subscribe") in identities_found


# ---------------------------------------------------------------------------
# Tests for format_drift_finding — direction-aware messages
# ---------------------------------------------------------------------------


def test_format_drift_finding_publish() -> None:
    msg = format_drift_finding(("hub", "_INBOX.>", "publish"))
    assert msg == "DRIFT: hub still publishes on _INBOX.> — should be _INBOX.hub.>"


def test_format_drift_finding_subscribe() -> None:
    msg = format_drift_finding(("telegram-adapter", "_INBOX.>", "subscribe"))
    expected = (
        "DRIFT: telegram-adapter still subscribes to _INBOX.>"
        " — should be _INBOX.telegram-adapter.>"
    )
    assert msg == expected


# ---------------------------------------------------------------------------
# Integration smoke — real acl-matrix.json must return [] (V3 narrowed)
# ---------------------------------------------------------------------------

_MATRIX_PATH = Path(__file__).parents[2] / "deploy" / "nats" / "acl-matrix.json"


def test_real_matrix_has_no_drift() -> None:
    """Acceptance criterion: the committed matrix is free of bare _INBOX.> for
    all lyra-owned identities.  Failing this test means a regression was
    introduced into deploy/nats/acl-matrix.json."""
    if not _MATRIX_PATH.exists():
        pytest.skip(f"matrix file not found: {_MATRIX_PATH}")
    identities = _load_matrix(_MATRIX_PATH)
    findings = audit_matrix_inbox_drift(identities)
    assert findings == [], (
        f"Residual bare _INBOX.> grants in acl-matrix.json: {findings}"
    )
