"""E2E test: validates code-required NATS subjects are covered by ACL grants.

Reads deploy/nats/acl-matrix.json directly and asserts the same coverage
that scripts/check_grants.py used to validate against code-subjects.json.

#1577 — derive ACL spec table parity fixture from acl-matrix.json.
"""

from __future__ import annotations

import copy
from pathlib import Path

from scripts._effective import effective_grants, subject_covered
from scripts._loader import load_matrix

# ── Paths ──────────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[2]
_REAL_MATRIX_JSON = _REPO / "deploy" / "nats" / "acl-matrix.json"


# ── Helpers ───────────────────────────────────────────────────────────────────


def _active_members(matrix, group_name: str) -> list[str]:
    return [
        name
        for name, identity in matrix["identities"].items()
        if identity["status"] == "active" and group_name in identity.get("groups", [])
    ]


# ── LYRA_OUTBOUND_AUDIO stream ───────────────────────────────────────────────


class TestLyraOutboundAudioStream:
    """LYRA_OUTBOUND_AUDIO stream — provisioner: hub, consumer_group: audio-consumer."""

    def test_hub_provisioner_publish_covers_stream_subjects(self) -> None:
        """hub publish[] covers the three LYRA_OUTBOUND_AUDIO provisioning subjects.

        # verified: if $JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO (or any of the
        other two) is removed from hub's publish grants, subject_covered still
        returns True because hub has the broad $JS.API.> wildcard.  The happy-path
        assertion therefore survives individual subject removal, but the test
        is still meaningful because it locks the *set* of required subjects into
        the test suite — any drift in the matrix (e.g. wildcard tightened) would
        be caught by the remaining assertions.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        grants = effective_grants(matrix)
        hub_pub, _ = grants["hub"]

        # Act + Assert
        subjects = [
            "$JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO",
            "$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO",
            "$JS.API.STREAM.UPDATE.LYRA_OUTBOUND_AUDIO",
        ]
        for subj in subjects:
            assert subject_covered(subj, hub_pub), (
                f"hub publish[] must cover {subj!r} "
                "for LYRA_OUTBOUND_AUDIO provisioning"
            )

    def test_audio_consumer_members_publish_covers_stream_subjects(self) -> None:
        """Every active audio-consumer member publish[] covers the required
        LYRA_OUTBOUND_AUDIO consumer subjects.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        grants = effective_grants(matrix)

        # Act + Assert
        subjects = [
            "$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO",
            "$JS.API.CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.>",
            "$JS.API.CONSUMER.INFO.LYRA_OUTBOUND_AUDIO.*",
            "$JS.API.CONSUMER.MSG.NEXT.LYRA_OUTBOUND_AUDIO.*",
            "$JS.ACK.LYRA_OUTBOUND_AUDIO.>",
        ]
        for member in _active_members(matrix, "audio-consumer"):
            m_pub, _ = grants[member]
            for subj in subjects:
                assert subject_covered(subj, m_pub), (
                    f"{member!r} publish[] must cover {subj!r} "
                    "for LYRA_OUTBOUND_AUDIO consumer"
                )

    def test_audio_consumer_publish_fails_when_subject_dropped(self) -> None:
        """Removing $JS.ACK.LYRA_OUTBOUND_AUDIO.> from audio-consumer group publish
        causes member effective grants to lose coverage.

        # verified: if the audio-consumer group grant is removed, the member's
        effective publish (group-expanded) no longer covers the subject.
        subject_covered returns False → assertion fails → test proves sensitivity.
        """
        # Arrange — pre-condition: subject must exist before we drop it
        matrix = load_matrix(_REAL_MATRIX_JSON)
        group_pub = matrix.get("groups", {})["audio-consumer"]["publish"]
        target = "$JS.ACK.LYRA_OUTBOUND_AUDIO.>"
        assert target in group_pub, (
            f"Pre-condition: {target!r} must be in audio-consumer group publish"
        )

        # Act — mutate a deep copy
        mutated = copy.deepcopy(matrix)
        mutated.get("groups", {})["audio-consumer"]["publish"].remove(target)
        grants = effective_grants(mutated)

        # Assert — each member now lacks coverage
        for member in _active_members(mutated, "audio-consumer"):
            m_pub, _ = grants[member]
            assert not subject_covered(target, m_pub), (
                f"{member!r} publish[] must NOT cover {target!r} "
                "after dropping it from audio-consumer group"
            )


# ── LYRA_TURNS stream ────────────────────────────────────────────────────────


class TestLyraTurnsStream:
    """LYRA_TURNS stream — provisioner: turn-writer, no consumer_group."""

    def test_turn_writer_provisioner_publish_covers_stream_subjects(self) -> None:
        """turn-writer publish[] covers all six LYRA_TURNS provisioning subjects.

        turn-writer has NO broad wildcard ($JS.API.> or >), so each subject is
        individually required. Removing any one would cause this assertion to fail.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        grants = effective_grants(matrix)
        tw_pub, _ = grants["turn-writer"]

        # Act + Assert
        subjects = [
            "$JS.API.STREAM.CREATE.LYRA_TURNS",
            "$JS.API.STREAM.INFO.LYRA_TURNS",
            "$JS.API.STREAM.UPDATE.LYRA_TURNS",
            "$JS.API.CONSUMER.CREATE.LYRA_TURNS.turn-writer-v1.>",
            "$JS.API.CONSUMER.INFO.LYRA_TURNS.turn-writer-v1",
            "$JS.API.CONSUMER.MSG.NEXT.LYRA_TURNS.turn-writer-v1",
        ]
        for subj in subjects:
            assert subject_covered(subj, tw_pub), (
                f"turn-writer publish[] must cover {subj!r} for LYRA_TURNS provisioning"
            )

    def test_turn_writer_provisioner_fails_when_subject_dropped(self) -> None:
        """Removing $JS.API.STREAM.CREATE.LYRA_TURNS from turn-writer publish
        causes subject_covered to return False.

        # verified: turn-writer has no broad wildcard, so the drop is not masked.
        If the subject is removed, the test assertion (assert not subject_covered)
        passes in the negative test, and the corresponding happy-path assertion
        in test_turn_writer_provisioner_publish_covers_stream_subjects would fail.
        """
        # Arrange — pre-condition
        matrix = load_matrix(_REAL_MATRIX_JSON)
        tw_publish = matrix["identities"]["turn-writer"]["publish"]
        target = "$JS.API.STREAM.CREATE.LYRA_TURNS"
        assert target in tw_publish, (
            f"Pre-condition: {target!r} must be in turn-writer publish"
        )
        assert "$JS.API.>" not in tw_publish, (
            "turn-writer must NOT have $JS.API.> — broad wildcard would mask the drop"
        )
        assert ">" not in tw_publish, (
            "turn-writer must NOT have bare > wildcard — it would mask the drop"
        )

        # Act — mutate a deep copy
        mutated = copy.deepcopy(matrix)
        mutated["identities"]["turn-writer"]["publish"].remove(target)
        grants = effective_grants(mutated)
        tw_pub, _ = grants["turn-writer"]

        # Assert
        assert not subject_covered(target, tw_pub), (
            f"turn-writer publish[] must NOT cover {target!r} after dropping it"
        )


# ── lyra_outbound_audio_sent KV bucket ───────────────────────────────────────


class TestLyraOutboundAudioSentKv:
    """lyra_outbound_audio_sent KV bucket — provisioner: hub,
    consumer_group: audio-consumer.
    """

    def test_hub_provisioner_publish_covers_kv_subjects(self) -> None:
        """hub publish[] covers the two KV_lyra_outbound_audio_sent provisioning
        subjects."""
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        grants = effective_grants(matrix)
        hub_pub, _ = grants["hub"]

        # Act + Assert
        subjects = [
            "$JS.API.STREAM.CREATE.KV_lyra_outbound_audio_sent",
            "$JS.API.STREAM.INFO.KV_lyra_outbound_audio_sent",
        ]
        for subj in subjects:
            assert subject_covered(subj, hub_pub), (
                f"hub publish[] must cover {subj!r} "
                "for KV_lyra_outbound_audio_sent provisioning"
            )

    def test_audio_consumer_members_publish_covers_kv_subjects(self) -> None:
        """Every active audio-consumer member publish[] covers the required
        KV_lyra_outbound_audio_sent consumer subjects.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        grants = effective_grants(matrix)

        # Act + Assert
        subjects = [
            "$JS.API.STREAM.INFO.KV_lyra_outbound_audio_sent",
            "$JS.API.STREAM.MSG.GET.KV_lyra_outbound_audio_sent",
            "$KV.lyra_outbound_audio_sent.>",
        ]
        for member in _active_members(matrix, "audio-consumer"):
            m_pub, _ = grants[member]
            for subj in subjects:
                assert subject_covered(subj, m_pub), (
                    f"{member!r} publish[] must cover {subj!r} "
                    "for KV_lyra_outbound_audio_sent consumer"
                )

    def test_audio_consumer_members_subscribe_covers_kv_subjects(self) -> None:
        """Every active audio-consumer member subscribe[] covers the KV bucket."""
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        grants = effective_grants(matrix)

        # Act + Assert
        subjects = ["$KV.lyra_outbound_audio_sent.>"]
        for member in _active_members(matrix, "audio-consumer"):
            _, m_sub = grants[member]
            for subj in subjects:
                assert subject_covered(subj, m_sub), (
                    f"{member!r} subscribe[] must cover {subj!r} "
                    "for KV_lyra_outbound_audio_sent consumer"
                )

    def test_audio_consumer_publish_fails_when_kv_subject_dropped(self) -> None:
        """Removing $KV.lyra_outbound_audio_sent.> from audio-consumer group publish
        causes member effective grants to lose coverage.

        # verified: the group grant is the sole source of this publish subject for
        members. Dropping it means subject_covered returns False for every member.
        """
        # Arrange — pre-condition
        matrix = load_matrix(_REAL_MATRIX_JSON)
        group_pub = matrix.get("groups", {})["audio-consumer"]["publish"]
        target = "$KV.lyra_outbound_audio_sent.>"
        assert target in group_pub, (
            f"Pre-condition: {target!r} must be in audio-consumer group publish"
        )

        # Act — mutate a deep copy
        mutated = copy.deepcopy(matrix)
        mutated.get("groups", {})["audio-consumer"]["publish"].remove(target)
        grants = effective_grants(mutated)

        # Assert
        for member in _active_members(mutated, "audio-consumer"):
            m_pub, _ = grants[member]
            assert not subject_covered(target, m_pub), (
                f"{member!r} publish[] must NOT cover {target!r} "
                "after dropping it from audio-consumer group"
            )

    def test_audio_consumer_subscribe_fails_when_kv_subject_dropped(self) -> None:
        """Removing $KV.lyra_outbound_audio_sent.> from audio-consumer group subscribe
        causes member effective grants to lose coverage.

        # verified: the group grant is the sole source of this subscribe subject.
        """
        # Arrange — pre-condition
        matrix = load_matrix(_REAL_MATRIX_JSON)
        group_sub = matrix.get("groups", {})["audio-consumer"]["subscribe"]
        target = "$KV.lyra_outbound_audio_sent.>"
        assert target in group_sub, (
            f"Pre-condition: {target!r} must be in audio-consumer group subscribe"
        )

        # Act — mutate a deep copy
        mutated = copy.deepcopy(matrix)
        mutated.get("groups", {})["audio-consumer"]["subscribe"].remove(target)
        grants = effective_grants(mutated)

        # Assert
        for member in _active_members(mutated, "audio-consumer"):
            _, m_sub = grants[member]
            assert not subject_covered(target, m_sub), (
                f"{member!r} subscribe[] must NOT cover {target!r} "
                "after dropping it from audio-consumer group"
            )

    def test_audio_consumer_has_active_members(self) -> None:
        """audio-consumer must have at least one active member; otherwise the
        consumer-group is dead and the code expecting consumers would fail at runtime.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)

        # Act
        members = _active_members(matrix, "audio-consumer")

        # Assert
        assert members, (
            "audio-consumer must have at least one active member; got none"
        )
        assert any(m in members for m in ("telegram-adapter", "discord-adapter")), (
            f"Expected telegram-adapter or discord-adapter "
            f"in audio-consumer; got: {members}"
        )
