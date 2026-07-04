"""Regression test: canonical factory.llm contract is in force at the broker layer.

Guards #1104. Two layers of assertions:
- structured: acl-matrix.json identity allow-lists (per-identity)
- rendered:   auth.conf has no legacy subjects

Static (no live server) — see tests/scripts/test_parity_e2e.py for the
matrix-driven live-nats-server ACL round-trip layer (#2247).
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
MATRIX = REPO / "deploy" / "nats" / "acl-matrix.json"
AUTH_CONF = REPO / "deploy" / "nats" / "auth.conf"


@pytest.fixture(scope="module")
def matrix() -> dict:
    assert MATRIX.exists(), f"acl-matrix.json missing: {MATRIX}"
    return json.loads(MATRIX.read_text())


@pytest.fixture(scope="module")
def auth_conf_text() -> str:
    assert AUTH_CONF.exists(), f"auth.conf missing: {AUTH_CONF}"
    return AUTH_CONF.read_text()


def test_hub_publishes_canonical_request(matrix: dict) -> None:
    assert "factory.llm.generate.request" in matrix["identities"]["hub"]["publish"]


def test_hub_subscribes_canonical_heartbeat(matrix: dict) -> None:
    assert "factory.llm.heartbeat" in matrix["identities"]["hub"]["subscribe"]


def test_llm_worker_subscribes_canonical_request(matrix: dict) -> None:
    allowed = matrix["identities"]["llm-worker"]["subscribe"]
    assert "factory.llm.generate.request" in allowed


def test_llm_worker_publishes_canonical_heartbeat(matrix: dict) -> None:
    assert "factory.llm.heartbeat" in matrix["identities"]["llm-worker"]["publish"]


def test_no_legacy_request_subject_in_auth_conf(auth_conf_text: str) -> None:
    # quoted "factory.llm.request" without ".generate." must not appear
    assert not re.search(r'"factory\.llm\.request"', auth_conf_text)


def test_no_legacy_heartbeat_pattern_in_auth_conf(auth_conf_text: str) -> None:
    assert "factory.llm.health.*" not in auth_conf_text


def test_matrix_no_legacy_subjects(matrix: dict) -> None:
    # Ensure no identity allow-list silently still references legacy
    for ident, body in matrix["identities"].items():
        for direction in ("publish", "subscribe"):
            for subj in body.get(direction, []):
                assert subj != "factory.llm.request", (
                    f"{ident}.{direction} has legacy factory.llm.request"
                )
                assert subj != "factory.llm.health.*", (
                    f"{ident}.{direction} has legacy factory.llm.health.*"
                )
