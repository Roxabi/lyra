"""Regression test: deploy/nats/auth.conf carries the canonical lyra.llm contract.

Guards against accidental rollback of #1104 — the regenerated auth.conf
must speak the canonical subjects (lyra.llm.generate.request, lyra.llm.heartbeat)
and must not contain the legacy ones (lyra.llm.request without .generate.,
lyra.llm.health.*).
"""
from __future__ import annotations

import re
from pathlib import Path

AUTH_CONF = Path(__file__).resolve().parents[2] / "deploy" / "nats" / "auth.conf"
TEXT = AUTH_CONF.read_text()


def test_canonical_request_subject_present() -> None:
    assert "lyra.llm.generate.request" in TEXT


def test_canonical_heartbeat_subject_present() -> None:
    assert "lyra.llm.heartbeat" in TEXT


def test_no_legacy_request_subject() -> None:
    # quoted literal "lyra.llm.request" without .generate. must not appear
    assert not re.search(r'"lyra\.llm\.request"', TEXT)


def test_no_legacy_heartbeat_pattern() -> None:
    assert "lyra.llm.health.*" not in TEXT
