"""Tests for scripts/check_grants.py — run() falsification gate.

#1527 S5 — CI falsification gate, ADR-079.

Tests exercise the core invariants of check_grants.run() and load_code_subjects():
  1. Unmutated real inputs → gate is green (no FAIL lines).
  2. Removing a provisioner subject from turn-writer → gate flips RED.
  3. Removing a consumer-group PUBLISH subject → gate flips RED.
  4. Removing a consumer-group SUBSCRIBE subject → gate flips RED (B3).
  5. An identity not in audio-consumer produces no consumer-group error.
  6. Membership gates the check: adding turn-writer to the group + dropping a
     subject confirms turn-writer appears in the failing members.
  7. Setting a non-existent provisioner in the manifest → gate flips RED.
  8. Removing audio-consumer from all identities → dead-group guard fires.
  9-11. load_code_subjects rejects malformed manifests (bidirectional validation).

Each negative case mutates a deep copy so tests do not bleed into each other.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path

import pytest
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
        assert _TARGET_SUBJECT in combined, (
            f"FAIL message must name the dropped subject {_TARGET_SUBJECT!r}; "
            f"got:\n{combined}"
        )


class TestCheckGrantsFailsMissingConsumerSubscribeSubject:
    def test_check_grants_fails_missing_consumer_subscribe_subject(self) -> None:
        """Removing $KV.lyra_outbound_audio_sent.> from the audio-consumer group's
        subscribe list causes run() to return a FAIL line naming an affected member
        (telegram-adapter or discord-adapter) and the dropped subject.

        $KV.lyra_outbound_audio_sent.> is the sole entry in both the group's
        subscribe list (matrix) and kv_buckets.lyra_outbound_audio_sent
        .consumer_subjects.subscribe (code-subjects manifest).  Removing it
        from the group means no member's effective subscribe grants cover it.

        # verified: if the subscribe for-loop in _check_consumer_group (L195-201 in
        check_grants.py) is deleted, run() returns [] even after this mutation —
        the gap is only caught by that loop.  With the loop present, the removal
        causes the gate to flip RED.
        """
        _TARGET_SUBJECT = "$KV.lyra_outbound_audio_sent.>"

        # Arrange — confirm the subject is in the group subscribe AND in the manifest
        matrix = load_matrix(_REAL_MATRIX_JSON)
        group_sub = matrix.get("groups", {})["audio-consumer"]["subscribe"]
        assert _TARGET_SUBJECT in group_sub, (
            f"Pre-condition: {_TARGET_SUBJECT!r} must be in "
            "audio-consumer group subscribe"
        )

        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)
        kv_consumer_sub = (
            code_subjects["kv_buckets"]["lyra_outbound_audio_sent"]
            .get("consumer_subjects", {})
            .get("subscribe", [])
        )
        assert _TARGET_SUBJECT in kv_consumer_sub, (
            f"Pre-condition: {_TARGET_SUBJECT!r} must be in "
            "kv_buckets.lyra_outbound_audio_sent consumer_subjects.subscribe"
        )

        # Act — mutate a deep copy: remove the subscribe subject from the group
        mutated = copy.deepcopy(matrix)
        mutated.get("groups", {})["audio-consumer"]["subscribe"].remove(_TARGET_SUBJECT)
        errors = run(mutated, code_subjects)

        # Assert — subscribe loop fires; gate is RED; an audio-consumer member is named
        assert len(errors) > 0, (
            "run() must return at least one FAIL line when "
            f"{_TARGET_SUBJECT!r} is removed from audio-consumer group subscribe"
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
        assert "subscribe[] does not cover" in combined, (
            "FAIL message must contain 'subscribe[] does not cover'; "
            f"got:\n{combined}"
        )
        assert _TARGET_SUBJECT in combined, (
            f"FAIL message must name the dropped subject {_TARGET_SUBJECT!r}; "
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

    def test_check_grants_membership_gates_consumer_group_check(self) -> None:
        """Membership in audio-consumer is what exposes an identity to the
        consumer-group check.  Adding turn-writer to the group, then removing
        a consumer subscribe subject from the group, causes run() to name
        turn-writer among the failing members.

        # verified: if the membership filter (the `cg in identity.get("groups", [])`
        condition in _check_consumer_group) were removed, ALL active identities
        would be checked regardless of membership — the test would no longer prove
        that membership is the gate.  With the filter present, turn-writer only
        appears in the FAIL output after it has been added to the group.
        """
        _TARGET_SUBJECT = "$KV.lyra_outbound_audio_sent.>"

        # Arrange — confirm turn-writer is NOT in audio-consumer initially
        matrix = load_matrix(_REAL_MATRIX_JSON)
        tw = matrix["identities"]["turn-writer"]
        assert tw["status"] == "active", "turn-writer must be active"
        assert "audio-consumer" not in tw.get("groups", []), (
            "Pre-condition: turn-writer must not be in audio-consumer"
        )
        code_subjects = load_code_subjects(_REAL_CODE_SUBJECTS_JSON)

        # Act — deep copy: add turn-writer to the group AND drop a subscribe
        #       subject so the gate has something to flag for the new member
        mutated = copy.deepcopy(matrix)
        mutated["identities"]["turn-writer"].setdefault("groups", []).append(
            "audio-consumer"
        )
        mutated.get("groups", {})["audio-consumer"]["subscribe"].remove(_TARGET_SUBJECT)
        errors = run(mutated, code_subjects)

        # Assert — turn-writer is now a member and appears among the failing identities
        assert len(errors) > 0, (
            "run() must return FAIL lines when audio-consumer has a dropped subscribe "
            "subject and turn-writer is now a member"
        )
        combined = "\n".join(errors)
        assert "turn-writer" in combined, (
            "FAIL message must name turn-writer now that it is a group member; "
            f"got:\n{combined}"
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


# ── Minimal valid shape used as baseline for malformed-manifest tests ──────────
#
# A minimal-valid code-subjects manifest has one stream or kv_bucket with:
#   - provisioner (str)
#   - provisioner_subjects.publish (list)
# The bidirectional consumer_group / consumer_subjects validation requires
# BOTH to be present or NEITHER.

_MINIMAL_VALID_STREAM = {
    "streams": {
        "TEST_STREAM": {
            "provisioner": "some-identity",
            "provisioner_subjects": {"publish": ["$JS.API.STREAM.CREATE.TEST_STREAM"]},
        }
    }
}


class TestLoadCodeSubjectsRejectsTopLevelArray:
    def test_load_code_subjects_rejects_top_level_array(
        self, tmp_path: Path
    ) -> None:
        """A code-subjects file whose top-level JSON value is an array must raise
        ValueError, not be silently accepted.

        # verified: if the 'if not isinstance(data, dict)' guard in
        load_code_subjects is deleted, json.loads returns the list and the
        subsequent .get("streams") / .get("kv_buckets") calls raise AttributeError
        (not ValueError). The guard ensures a clean, typed error.  Deleting it
        causes this test to raise AttributeError rather than ValueError, so
        pytest.raises(ValueError) would fail → test is non-tautological.
        """
        # Arrange — write a top-level array (valid JSON, wrong shape)
        bad_file = tmp_path / "code-subjects.json"
        bad_file.write_text(json.dumps([{"provisioner": "hub"}]))

        # Act + Assert
        with pytest.raises(ValueError, match="top-level value must be a JSON object"):
            load_code_subjects(bad_file)


class TestLoadCodeSubjectsRejectsStreamWithConsumerSubjectsButNoGroup:
    def test_load_code_subjects_rejects_stream_with_consumer_subjects_but_no_group(
        self, tmp_path: Path
    ) -> None:
        """A stream that has consumer_subjects (with a non-empty publish list) but
        no consumer_group must raise ValueError.

        This locks in the B2 bidirectional validation: consumer_subjects without
        consumer_group is a half-wired resource that check_grants.run() cannot
        cross-check (it would silently skip the consumer-group loop).

        # verified: if the 'if has_subjects and not has_group' guard in
        load_code_subjects is deleted, the manifest is accepted and run() skips
        the consumer-group check entirely for this resource — a silent gap.
        Deleting the guard means load_code_subjects returns successfully, the
        test's pytest.raises(ValueError) block would NOT trigger, and the test
        fails → non-tautological.
        """
        # Arrange — stream has consumer_subjects.publish but no consumer_group
        manifest = copy.deepcopy(_MINIMAL_VALID_STREAM)
        manifest["streams"]["TEST_STREAM"]["consumer_subjects"] = {
            "publish": ["$JS.ACK.TEST_STREAM.>"]
        }
        # consumer_group intentionally absent

        bad_file = tmp_path / "code-subjects.json"
        bad_file.write_text(json.dumps(manifest))

        # Act + Assert
        with pytest.raises(ValueError, match="'consumer_group' is absent"):
            load_code_subjects(bad_file)


class TestLoadCodeSubjectsRejectsStreamWithConsumerGroupButNoSubjects:
    def test_load_code_subjects_rejects_stream_with_consumer_group_but_no_subjects(
        self, tmp_path: Path
    ) -> None:
        """A stream that has consumer_group but no consumer_subjects (or an empty
        consumer_subjects dict) must raise ValueError.

        This locks in the other half of the B2 bidirectional validation:
        consumer_group without consumer_subjects leaves check_grants.run() with a
        group to check but nothing to check against — the loop would be a no-op
        and the wiring would go unverified.

        # verified: if the 'if has_group and not has_subjects' guard in
        load_code_subjects is deleted, the manifest is accepted and run() iterates
        members but checks zero subjects, producing [] even for identities that lack
        all consumer grants.  Deleting the guard means load_code_subjects returns
        successfully, pytest.raises(ValueError) does not trigger → test fails →
        non-tautological.
        """
        # Arrange — stream has consumer_group but no consumer_subjects at all
        manifest = copy.deepcopy(_MINIMAL_VALID_STREAM)
        manifest["streams"]["TEST_STREAM"]["consumer_group"] = "some-group"
        # consumer_subjects intentionally absent

        bad_file = tmp_path / "code-subjects.json"
        bad_file.write_text(json.dumps(manifest))

        # Act + Assert
        with pytest.raises(ValueError, match="'consumer_subjects' is absent"):
            load_code_subjects(bad_file)
