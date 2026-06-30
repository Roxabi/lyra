"""CI negative test — forbidden attrs never exported."""

from __future__ import annotations

from roxabi_otel import scrub_attrs


def test_forbidden_attrs_stripped() -> None:
    raw = {
        "roxabi.job_id": "a" * 32,
        "audio_b64": "AAAA",
        "messages": "[]",
        "user.email": "a@b.c",
    }
    cleaned, dropped = scrub_attrs(raw)
    assert cleaned == {"roxabi.job_id": "a" * 32}
    assert dropped == 3