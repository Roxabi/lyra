"""Tests for Guard protocol, BlockedGuard, and GuardChain."""

from __future__ import annotations

from lyra.core.guard import BlockedGuard, GuardChain, Rejection
from lyra.core.identity import Identity
from lyra.core.trust import TrustLevel


def _id(trust: TrustLevel, *, admin: bool = False) -> Identity:
    return Identity(user_id="u1", trust_level=trust, is_admin=admin)


class TestBlockedGuard:
    def test_rejects_blocked(self) -> None:
        result = BlockedGuard().check(_id(TrustLevel.BLOCKED))
        assert result == Rejection(reason="blocked")

    def test_passes_owner(self) -> None:
        assert BlockedGuard().check(_id(TrustLevel.OWNER)) is None

    def test_passes_trusted(self) -> None:
        assert BlockedGuard().check(_id(TrustLevel.TRUSTED)) is None

    def test_passes_public(self) -> None:
        assert BlockedGuard().check(_id(TrustLevel.PUBLIC)) is None


class TestGuardChain:
    def test_empty_chain_passes(self) -> None:
        chain = GuardChain([])
        assert chain.run(_id(TrustLevel.BLOCKED)) is None

    def test_single_guard_rejects(self) -> None:
        chain = GuardChain([BlockedGuard()])
        result = chain.run(_id(TrustLevel.BLOCKED))
        assert result == Rejection(reason="blocked")

    def test_single_guard_passes(self) -> None:
        chain = GuardChain([BlockedGuard()])
        assert chain.run(_id(TrustLevel.OWNER)) is None

    def test_short_circuits_on_first_rejection(self) -> None:
        class AlwaysReject:
            def check(self, identity: Identity) -> Rejection | None:
                return Rejection(reason="custom")

        class NeverCalled:
            def __init__(self) -> None:
                self.called = False

            def check(self, identity: Identity) -> Rejection | None:
                self.called = True
                return None

        never = NeverCalled()
        chain = GuardChain([AlwaysReject(), never])
        result = chain.run(_id(TrustLevel.OWNER))
        assert result == Rejection(reason="custom")
        assert never.called is False

    def test_all_pass_returns_none(self) -> None:
        class PassGuard:
            def check(self, identity: Identity) -> Rejection | None:
                return None

        chain = GuardChain([PassGuard(), PassGuard()])
        assert chain.run(_id(TrustLevel.PUBLIC)) is None
