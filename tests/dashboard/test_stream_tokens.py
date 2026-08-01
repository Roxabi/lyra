"""Multi-slot stream token registry (#2316)."""

from __future__ import annotations

from factory.dashboard.stream_tokens import StreamTokenRegistry


def test_mint_verify_roundtrip() -> None:
    reg = StreamTokenRegistry()
    tok = reg.mint("jobs")
    assert reg.verify("jobs", tok)
    assert not reg.verify("pipeline", tok)
    assert not reg.verify("jobs", "bogus")
    assert not reg.verify("jobs", None)


def test_concurrent_mint_does_not_clobber_peer() -> None:
    reg = StreamTokenRegistry()
    a = reg.mint("jobs")
    b = reg.mint("jobs")
    assert a != b
    assert reg.verify("jobs", a)
    assert reg.verify("jobs", b)


def test_revoke_token_leaves_peer() -> None:
    reg = StreamTokenRegistry()
    a = reg.mint("jobs")
    b = reg.mint("jobs")
    reg.revoke_token(a)
    assert not reg.verify("jobs", a)
    assert reg.verify("jobs", b)


def test_revoke_stream_clears_all_slots() -> None:
    """Chat session close — revoke(session_id) clears every token for that key."""
    reg = StreamTokenRegistry()
    a = reg.mint("sess-1")
    b = reg.mint("sess-1")
    other = reg.mint("sess-2")
    reg.revoke("sess-1")
    assert not reg.verify("sess-1", a)
    assert not reg.verify("sess-1", b)
    assert reg.verify("sess-2", other)


def test_revoke_token_noop_on_unknown() -> None:
    reg = StreamTokenRegistry()
    reg.revoke_token(None)
    reg.revoke_token("not-a-token")
