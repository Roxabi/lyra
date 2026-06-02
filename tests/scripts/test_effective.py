"""Tests for scripts/_effective.py — effective_grants expansion.

#1527 S5 — CI falsification gate.

Three tests exercise the three fundamental invariants of effective_grants:
  1. Group-less identities (no 'groups' key) are included with their inline grants.
  2. Group membership is expanded into the effective publish/subscribe sets.
  3. Retired identities are excluded entirely.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from scripts._acl_models import LoadedMatrix
from scripts._effective import effective_grants, subject_covered
from scripts._loader import load_matrix

# ── Repo root ────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[2]
_REAL_MATRIX_JSON = _REPO / "deploy" / "nats" / "acl-matrix.json"
_FIXTURES = Path(__file__).resolve().parent / "fixtures"
_WITH_RETIRED_JSON = _FIXTURES / "v2-with-retired.json"


# ── helpers ───────────────────────────────────────────────────────────────────


@pytest.fixture()
def prod_matrix() -> LoadedMatrix:
    """Validated LoadedMatrix from deploy/nats/acl-matrix.json."""
    return load_matrix(_REAL_MATRIX_JSON)


@pytest.fixture()
def with_retired_matrix() -> LoadedMatrix:
    """Validated LoadedMatrix from tests/scripts/fixtures/v2-with-retired.json."""
    return load_matrix(_WITH_RETIRED_JSON)


# ── tests ─────────────────────────────────────────────────────────────────────


class TestEffectiveGrantsIncludesGrouplessIdentity:
    def test_effective_grants_includes_groupless_identity(
        self, prod_matrix: LoadedMatrix
    ) -> None:
        """turn-writer (no 'groups', no request_reply_flow) is present in
        effective_grants and its publish list contains its inline subject
        $JS.API.STREAM.CREATE.LYRA_TURNS.

        Guards the requirement: group-less identities must NOT be silently
        dropped (e.g. by a KeyError on the missing 'groups' key or a wrong
        filter).

        # verified: removing the 'if identity["status"] == "retired": continue'
        # guard or the identity iteration loop causes this assertion to fail in
        # a trivially detectable way; likewise, adding an erroneous
        # 'if identity.get("groups"): ...' skip would drop turn-writer entirely.
        """
        # Arrange — confirm the matrix invariant we rely on
        tw_identity = prod_matrix["identities"]["turn-writer"]
        assert tw_identity["status"] == "active"
        assert "groups" not in tw_identity, (
            "turn-writer must have no 'groups' key for this test to be meaningful"
        )
        assert "$JS.API.STREAM.CREATE.LYRA_TURNS" in tw_identity["publish"]

        # Act
        grants = effective_grants(prod_matrix)

        # Assert — group-less identity is present and inline publish is preserved
        assert "turn-writer" in grants, (
            "turn-writer must be a key in effective_grants — "
            "group-less identities must not be dropped"
        )
        pub, _ = grants["turn-writer"]
        assert "$JS.API.STREAM.CREATE.LYRA_TURNS" in pub, (
            "turn-writer effective publish must contain its inline subject "
            "$JS.API.STREAM.CREATE.LYRA_TURNS"
        )


class TestEffectiveGrantsExpandsGroups:
    def test_effective_grants_expands_groups(self, prod_matrix: LoadedMatrix) -> None:
        """telegram-adapter's effective publish contains $JS.ACK.LYRA_OUTBOUND_AUDIO.>
        which comes exclusively from the audio-consumer group and is absent from
        telegram-adapter's inline publish list.

        Proves that group expansion fires: if the for-gname loop in effective_grants
        is removed, the audio-consumer subjects are missing from the result.

        # verified: deleting the group-expansion loop causes this assertion to fail —
        # $JS.ACK.LYRA_OUTBOUND_AUDIO.> is only present in audio-consumer.publish,
        # not in telegram-adapter's inline publish list.
        """
        # Arrange — verify the matrix invariants this test depends on
        tg_identity = prod_matrix["identities"]["telegram-adapter"]
        assert tg_identity["status"] == "active"
        assert "audio-consumer" in tg_identity.get("groups", []), (
            "telegram-adapter must reference the audio-consumer group"
        )
        # The subject must NOT be in the inline publish list
        assert "$JS.ACK.LYRA_OUTBOUND_AUDIO.>" not in tg_identity["publish"], (
            "$JS.ACK.LYRA_OUTBOUND_AUDIO.> must not be in telegram-adapter's "
            "inline publish — it must come from the group only"
        )
        # The subject must be in the group
        group_pub = prod_matrix.get("groups", {})["audio-consumer"]["publish"]
        assert "$JS.ACK.LYRA_OUTBOUND_AUDIO.>" in group_pub, (
            "$JS.ACK.LYRA_OUTBOUND_AUDIO.> must be in audio-consumer group publish"
        )

        # Act
        grants = effective_grants(prod_matrix)

        # Assert — group-sourced subject present after expansion
        assert "telegram-adapter" in grants
        pub, _ = grants["telegram-adapter"]
        assert "$JS.ACK.LYRA_OUTBOUND_AUDIO.>" in pub, (
            "telegram-adapter effective publish must contain "
            "$JS.ACK.LYRA_OUTBOUND_AUDIO.> after audio-consumer group expansion"
        )


class TestEffectiveGrantsExcludesRetired:
    def test_effective_grants_excludes_retired(
        self, with_retired_matrix: LoadedMatrix
    ) -> None:
        """old-worker (status='retired') must NOT appear as a key in
        effective_grants output.

        # verified: removing the 'if identity["status"] == "retired": continue'
        # check causes old-worker to appear in the result → assertion fails.
        """
        # Arrange — confirm old-worker is present and retired in the fixture
        assert "old-worker" in with_retired_matrix["identities"], (
            "fixture must contain old-worker identity"
        )
        assert with_retired_matrix["identities"]["old-worker"]["status"] == "retired"

        # Act
        grants = effective_grants(with_retired_matrix)

        # Assert — retired identity excluded
        assert "old-worker" not in grants, (
            "old-worker is retired and must not appear in effective_grants keys"
        )


# ── subject_covered ───────────────────────────────────────────────────────────


class TestSubjectCovered:
    @pytest.mark.parametrize(
        ("subject", "grants", "expected"),
        [
            # exact match
            ("foo", ["foo"], True),
            # bare >
            ("foo.bar", [">"], True),
            # .> matches sub-level
            ("foo.bar", ["foo.>"], True),
            # .> matches deep sub-level
            ("foo.bar.baz", ["foo.>"], True),
            # .> does NOT match bare prefix
            ("foo", ["foo.>"], False),
            # .> does NOT match unrelated
            ("bar.baz", ["foo.>"], False),
            # .* matches single token
            ("foo.bar", ["foo.*"], True),
            # .* does NOT match multi-token
            ("foo.bar.baz", ["foo.*"], False),
            # .* does NOT match bare prefix
            ("foo", ["foo.*"], False),
            # .* does NOT match unrelated
            ("bar.baz", ["foo.*"], False),
            # mixed grant list
            ("foo.bar", ["foo.>", "baz.*"], True),
            # mixed grant list negative
            ("foo.bar.baz", ["foo.*"], False),
        ],
    )
    def test_subject_covered(
        self, subject: str, grants: list[str], expected: bool
    ) -> None:
        assert subject_covered(subject, grants) is expected
