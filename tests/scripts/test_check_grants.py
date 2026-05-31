"""Tests for scripts/check_grants.py — run() falsification gate.

#1527 S5 — CI falsification gate, ADR-079.

Six tests exercise the core invariants of check_grants.run():
  1. Unmutated real inputs → gate is green (no FAIL lines).
  2. Removing a provisioner subject from turn-writer → gate flips RED.
  3. Removing a consumer-group subject from audio-consumer group → gate flips RED.
  4. An identity not in audio-consumer produces no consumer-group error.
  5. Setting a non-existent provisioner in the manifest → gate flips RED.
  6. Removing audio-consumer from all identities → dead-group guard fires.

Each negative case mutates a deep copy so tests do not bleed into each other.
"""

from __future__ import annotations

import copy
from pathlib import Path

from scripts._loader import load_matrix
from scripts.check_grants import load_code_subjects, run

# ── Paths ─────────────────────────────────────────────────────────────────────
_REPO = Path(__file__).resolve().parents[2]
_REAL_MATRIX_JSON = _REPO / "deploy" / "nats" / "acl-matrix.json"
_REAL_CODE_SUBJECTS_JSON = _REPO / "deploy" / "nats" / "code-subjects.json"


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCheckGrantsPassesOnValidMatrix:
    def test_check_grants_passes_on_valid_matrix(self) -> None:
        """Unmutated real matrix + manifest → run() returns [] (gate green).

        # verified: if run() returns non-empty on the real inputs, CI would be
        # permanently broken — this asserts the baseline is healthy today.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)

        # Act
        errors = run(matrix, code_subjects)

        # Assert
        assert errors == [], (
            "check_grants.run() must return [] on unmodified real inputs; "
            "got:\n" + "\n".join(errors)
        )


class TestCheckGrantsFailsMissingProvisionerSubject:
    def test_check_grants_fails_missing_provisioner_subject(self) -> None:
        """Removing $JS.API.STREAM.CREATE.LYRA_TURNS from turn-writer publish
        causes run() to return a FAIL line mentioning that subject.

        Why turn-writer (not hub): hub has $JS.API.> which covers everything —
        removing one explicit subject would be masked. turn-writer has only
        scoped subjects with no broad wildcard, so removing one is not covered.

        # verified: if the _check_provisioner guard is deleted, run() returns []
        even after this mutation, making the test tautological. With the guard,
        the test fails the assertion only when the subject IS covered — i.e.,
        when we do NOT mutate. The mutation causes the guard to fire.
        """
        _TARGET_SUBJECT = "$JS.API.STREAM.CREATE.LYRA_TURNS"

        # Arrange — confirm turn-writer has no broad wildcard before mutating
        matrix = load_matrix(_REAL_MATRIX_JSON)
        tw_publish = matrix["identities"]["turn-writer"]["publish"]
        assert _TARGET_SUBJECT in tw_publish, (
            "Pre-condition: turn-writer must publish $JS.API.STREAM.CREATE.LYRA_TURNS"
        )
        assert "$JS.API.>" not in tw_publish, (
            "turn-writer must NOT have $JS.API.> — broad wildcard would mask the drop"
        )
        assert ">" not in tw_publish, (
            "turn-writer must NOT have bare > wildcard — it would mask the drop"
        )

        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)

        # Act — mutate a deep copy
        mutated = copy.deepcopy(matrix)
        mutated["identities"]["turn-writer"]["publish"].remove(_TARGET_SUBJECT)
        errors = run(mutated, code_subjects)

        # Assert — gate flips RED with a message naming the missing subject
        assert len(errors) > 0, (
            "run() must return at least one FAIL line when "
            f"{_TARGET_SUBJECT!r} is removed from turn-writer publish"
        )
        combined = "\n".join(errors)
        assert "LYRA_TURNS" in combined, (
            f"FAIL message must mention LYRA_TURNS; got:\n{combined}"
        )
        assert _TARGET_SUBJECT in combined, (
            f"FAIL message must mention the missing subject {_TARGET_SUBJECT!r}; "
            f"got:\n{combined}"
        )
        assert "does not cover required subject" in combined, (
            "FAIL message must contain 'does not cover required subject'; "
            f"got:\n{combined}"
        )


class TestCheckGrantsFailsMissingConsumerSubject:
    def test_check_grants_fails_missing_consumer_subject(self) -> None:
        """Removing $JS.ACK.LYRA_OUTBOUND_AUDIO.> from the audio-consumer group's
        publish in the matrix causes run() to return a FAIL line naming an
        affected member identity (telegram-adapter or discord-adapter).

        The audio-consumer group is the sole source of $JS.ACK.LYRA_OUTBOUND_AUDIO.>
        for its members. The LYRA_OUTBOUND_AUDIO stream's consumer_subjects.publish
        requires this subject. Removing it from the group means neither member's
        effective grants cover it.

        # verified: if the _check_consumer_group guard is deleted, run() returns []
        even after this mutation — the removal only matters because the guard
        cross-checks member effective grants against consumer_subjects.
        """
        _TARGET_SUBJECT = "$JS.ACK.LYRA_OUTBOUND_AUDIO.>"

        # Arrange — verify the subject is in the group and in consumer_subjects
        matrix = load_matrix(_REAL_MATRIX_JSON)
        group_pub = matrix.get("groups", {})["audio-consumer"]["publish"]
        assert _TARGET_SUBJECT in group_pub, (
            f"Pre-condition: {_TARGET_SUBJECT!r} must be in "
            "audio-consumer group publish"
        )

        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)
        stream_consumer_pub = (
            code_subjects["streams"]["LYRA_OUTBOUND_AUDIO"]
            .get("consumer_subjects", {})
            .get("publish", [])
        )
        assert _TARGET_SUBJECT in stream_consumer_pub, (
            f"Pre-condition: {_TARGET_SUBJECT!r} must be in "
            "LYRA_OUTBOUND_AUDIO consumer_subjects.publish"
        )

        # Act — mutate a deep copy (remove from group, not from member inline lists)
        mutated = copy.deepcopy(matrix)
        mutated.get("groups", {})["audio-consumer"]["publish"].remove(_TARGET_SUBJECT)
        errors = run(mutated, code_subjects)

        # Assert — gate flips RED naming an audio-consumer member
        assert len(errors) > 0, (
            "run() must return at least one FAIL line when "
            f"{_TARGET_SUBJECT!r} is removed from audio-consumer group publish"
        )
        combined = "\n".join(errors)
        assert any(
            member in combined
            for member in ("telegram-adapter", "discord-adapter")
        ), (
            "FAIL message must name an audio-consumer member "
            "(telegram-adapter or discord-adapter); "
            f"got:\n{combined}"
        )
        assert "does not cover required subject" in combined, (
            "FAIL message must contain 'does not cover required subject'; "
            f"got:\n{combined}"
        )


class TestCheckGrantsPassesIdentityNotInGroup:
    def test_check_grants_passes_identity_not_in_group(self) -> None:
        """An active identity NOT in audio-consumer (turn-writer) produces no
        consumer-group error in the unmutated output.

        This confirms non-members are not checked against consumer_subjects —
        the consumer-group check only iterates identities whose groups list
        includes the relevant consumer_group name.

        # verified: if the member-filter condition in _check_consumer_group were
        removed (all active identities checked), turn-writer would fail because
        it lacks audio-consumer grants — and this assertion would catch that.
        """
        # Arrange — confirm turn-writer is active and NOT in audio-consumer
        matrix = load_matrix(_REAL_MATRIX_JSON)
        tw = matrix["identities"]["turn-writer"]
        assert tw["status"] == "active", "turn-writer must be active"
        assert "audio-consumer" not in tw.get("groups", []), (
            "turn-writer must not be in audio-consumer for this test to be meaningful"
        )

        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)

        # Act
        errors = run(matrix, code_subjects)

        # Assert — no FAIL line names turn-writer as a consumer-group member
        consumer_group_errors = [
            e for e in errors
            if "turn-writer" in e and "consumer-group" in e
        ]
        assert consumer_group_errors == [], (
            "turn-writer must produce no consumer-group FAIL lines "
            "(it is not in audio-consumer); got:\n"
            + "\n".join(consumer_group_errors)
        )


class TestCheckGrantsFailsMissingProvisionerIdentity:
    def test_check_grants_fails_missing_provisioner_identity(self) -> None:
        """Setting LYRA_TURNS provisioner to 'ghost-writer' (absent from the matrix)
        causes run() to return a FAIL line indicating the provisioner is missing
        or retired.

        # verified: if the 'if provisioner not in grants' guard in _check_provisioner
        is deleted, run() would raise a KeyError (or return []) — without the guard
        there is no soft FAIL path. With the guard, the mutation is caught cleanly.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)

        # Confirm ghost-writer is absent from the matrix
        assert "ghost-writer" not in matrix["identities"], (
            "Pre-condition: 'ghost-writer' must not exist in the matrix"
        )

        # Act — mutate a deep copy of the manifest
        mutated_cs = copy.deepcopy(code_subjects)
        mutated_cs["streams"]["LYRA_TURNS"]["provisioner"] = "ghost-writer"
        errors = run(matrix, mutated_cs)

        # Assert — gate flips RED with "missing or retired"
        assert len(errors) > 0, (
            "run() must return at least one FAIL line when provisioner "
            "'ghost-writer' is absent from the matrix"
        )
        combined = "\n".join(errors)
        assert "missing or retired" in combined, (
            "FAIL message must contain 'missing or retired'; "
            f"got:\n{combined}"
        )
        assert "ghost-writer" in combined, (
            "FAIL message must name the ghost provisioner 'ghost-writer'; "
            f"got:\n{combined}"
        )


class TestCheckGrantsFailsDeadConsumerGroup:
    def test_check_grants_fails_dead_consumer_group(self) -> None:
        """Removing audio-consumer from every identity's groups list in the matrix
        (so the group has no active members) while the manifest still references
        consumer_group: audio-consumer causes run() to return a FAIL line
        containing 'no active members'.

        # verified: if the dead-group guard ('if not members: return [FAIL ...]')
        in _check_consumer_group is deleted, run() returns [] after this mutation
        (there are no members to iterate over). With the guard, the absence of
        members is explicitly flagged.
        """
        # Arrange
        matrix = load_matrix(_REAL_MATRIX_JSON)
        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)

        # Confirm at least one identity currently references audio-consumer
        members_before = [
            name
            for name, ident in matrix["identities"].items()
            if ident.get("status") == "active"
            and "audio-consumer" in ident.get("groups", [])
        ]
        assert len(members_before) > 0, (
            "Pre-condition: at least one active identity must be in audio-consumer"
        )

        # Confirm the manifest still references audio-consumer
        audio_stream = code_subjects["streams"]["LYRA_OUTBOUND_AUDIO"]
        assert audio_stream.get("consumer_group") == "audio-consumer", (
            "Pre-condition: LYRA_OUTBOUND_AUDIO must reference "
            "consumer_group audio-consumer"
        )

        # Act — mutate a deep copy: strip audio-consumer from all identities
        mutated = copy.deepcopy(matrix)
        for ident in mutated["identities"].values():
            groups = ident.get("groups", [])
            if "audio-consumer" in groups:
                groups.remove("audio-consumer")

        errors = run(mutated, code_subjects)

        # Assert — dead-group guard fires
        assert len(errors) > 0, (
            "run() must return at least one FAIL line when audio-consumer "
            "has no active members"
        )
        combined = "\n".join(errors)
        assert "no active members" in combined, (
            "FAIL message must contain 'no active members'; "
            f"got:\n{combined}"
        )
        assert "audio-consumer" in combined, (
            "FAIL message must name the dead group 'audio-consumer'; "
            f"got:\n{combined}"
        )
