"""Unit tests for TrustLevel enum (issue #151, S1)."""

from __future__ import annotations

from lyra.core.trust import TrustLevel

# ---------------------------------------------------------------------------
# TestTrustLevel
# ---------------------------------------------------------------------------


class TestTrustLevel:
    def test_four_members(self) -> None:
        assert len(TrustLevel) == 4

    def test_str_values(self) -> None:
        assert TrustLevel.OWNER.value == "owner"
        assert TrustLevel.TRUSTED.value == "trusted"
        assert TrustLevel.PUBLIC.value == "public"
        assert TrustLevel.BLOCKED.value == "blocked"

    def test_is_str_subclass(self) -> None:
        assert isinstance(TrustLevel.OWNER, str)
        assert TrustLevel.OWNER == "owner"

    def test_all_members_present(self) -> None:
        names = {m.name for m in TrustLevel}
        assert names == {"OWNER", "TRUSTED", "PUBLIC", "BLOCKED"}
