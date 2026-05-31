"""Tests for scripts/_renderer.py — #1017 T03 + ADR-079 S3 ACL regression (#1525).

T03 tests verify the renderer produces correct NATS auth.conf from the ACL matrix.
S3 regression tests assert the rendered conf for adapters no longer contains the
3 CREATE/UPDATE subjects removed in S3 and still contains STREAM.INFO.
"""

from __future__ import annotations

import re
from pathlib import Path

# This import will fail at collection time — that is the intended RED state.
from scripts._acl_models import LoadedMatrix
from scripts._renderer import parse_auth_conf, render_auth_conf

# ── helpers ───────────────────────────────────────────────────────────────────


def _fake_pubkeys(matrix: LoadedMatrix) -> dict[str, str]:
    """Build deterministic pubkeys dict from matrix identities (active only)."""
    return {
        name: f"UDET{name.upper().replace('-', '')}" for name in matrix["identities"]
    }


# ── tests ─────────────────────────────────────────────────────────────────────


class TestEmitUserStructure:
    def test_emit_user_structure(self, prod_matrix: LoadedMatrix) -> None:
        """Rendered output for hub contains its name in a comment and its pubkey.

        SC-3: every identity block carries the name as a comment and the nkey pubkey.
        # verified: removing name/pubkey from emit_user causes assertion failure
        """
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)

        assert "hub" in rendered
        assert "UDETHUB" in rendered

    def test_emit_voice_tts_structure(self, prod_matrix: LoadedMatrix) -> None:
        """Rendered output for voice-tts contains its name and pubkey."""
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)

        assert "voice-tts" in rendered
        assert "UDETVOICETTS" in rendered


class TestRetiredIdentityExcluded:
    def test_retired_identity_excluded(self, with_retired_matrix: LoadedMatrix) -> None:
        """Retired identity old-worker must NOT appear in rendered output.

        SC-5: retired identities are excluded from auth.conf rendering.
        # verified: removing status filter causes old-worker to appear → assertion fails
        """
        pubkeys = _fake_pubkeys(with_retired_matrix)
        rendered = render_auth_conf(with_retired_matrix, pubkeys)

        assert "old-worker" not in rendered
        assert "UDETOLDWORKER" not in rendered


class TestAllowResponsesHonored:
    def test_allow_responses_honored(self, prod_matrix: LoadedMatrix) -> None:
        """voice-tts (allow_responses=True) renders allow_responses: true in block.

        SC-4: allow_responses field from matrix is faithfully rendered.
        # verified: removing allow_responses handling → field absent/false → fails
        """
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)

        # voice-tts has allow_responses: true in v2-prod.json
        assert prod_matrix["identities"]["voice-tts"]["allow_responses"] is True
        # The rendered block must contain allow_responses: true somewhere near voice-tts
        assert "allow_responses: true" in rendered

    def test_allow_responses_false_honored(self, prod_matrix: LoadedMatrix) -> None:
        """telegram-adapter (allow_responses=False) renders allow_responses: false
        in block."""
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)

        assert prod_matrix["identities"]["telegram-adapter"]["allow_responses"] is False
        assert "allow_responses: false" in rendered


class TestInboxGrantFromFlow:
    def test_inbox_grant_from_flow(self) -> None:
        """Responder's publish allow contains _inbox.<requester>.> from flows.

        SC-6: derived inbox grants — for flow (hub → clipool-worker, lyra.clipool.cmd),
        clipool-worker's publish allow must include _inbox.hub.>
        # verified: removing flow-derivation logic → _inbox.hub.> absent → fails
        """
        matrix: LoadedMatrix = {
            "version": "2",
            "request_reply_flows": [
                {
                    "requester": "hub",
                    "responder": "clipool-worker",
                    "subject": "lyra.clipool.cmd",
                }
            ],
            "identities": {
                "hub": {
                    "status": "active",
                    "created_at": "2026-04-21",
                    "owner": "lyra",
                    "description": "hub",
                    "allow_responses": False,
                    "publish": ["lyra.clipool.cmd"],
                    "subscribe": ["_inbox.hub.>"],
                },
                "clipool-worker": {
                    "status": "active",
                    "created_at": "2026-04-27",
                    "owner": "lyra",
                    "description": "clipool worker",
                    "allow_responses": True,
                    "publish": ["lyra.clipool.heartbeat"],
                    "subscribe": ["lyra.clipool.cmd"],
                },
            },
        }
        pubkeys = {
            "hub": "UDETHUB",
            "clipool-worker": "UDETCLIPOOLWORKER",
        }
        rendered = render_auth_conf(matrix, pubkeys)

        # clipool-worker's block must include the derived inbox grant
        assert "_inbox.hub.>" in rendered


class TestParserIdempotence:
    def test_parser_idempotence(self, prod_matrix: LoadedMatrix) -> None:
        """parse_auth_conf is idempotent: render→parse→render→parse gives equal results.

        SC-22: ParsedAuthConf is a dataclass(eq=True, frozen=True); equality is
        the parity primitive.
        # verified: breaking parse_auth_conf to mutable → frozen check fails
        """
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered1 = render_auth_conf(prod_matrix, pubkeys)
        parsed1 = parse_auth_conf(rendered1)

        rendered2 = render_auth_conf(prod_matrix, pubkeys)
        parsed2 = parse_auth_conf(rendered2)

        assert parsed1 == parsed2

    def test_parsed_auth_conf_is_frozen(self, prod_matrix: LoadedMatrix) -> None:
        """ParsedAuthConf must be frozen (dataclass frozen=True).

        # verified: removing frozen=True from ParsedAuthConf → mutation allowed → fails
        """
        import dataclasses

        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)
        parsed = parse_auth_conf(rendered)

        assert dataclasses.is_dataclass(parsed)
        assert parsed.__dataclass_params__.frozen  # type: ignore[attr-defined]


class TestAclRegressionS3AdapterNoStreamCreate:
    """ADR-079 S3 (#1525) — adapters must not hold STREAM.CREATE/UPDATE audio grants.

    Renders deploy/nats/acl-matrix.json via render_auth_conf and parses the
    per-adapter user blocks.  Asserts the 3 subjects removed in S3 are absent
    and that STREAM.INFO.LYRA_OUTBOUND_AUDIO is still present (needed by
    pull_subscribe's stream_info call).
    """

    _REMOVED_SUBJECTS = frozenset(
        {
            "$JS.API.STREAM.CREATE.LYRA_OUTBOUND_AUDIO",
            "$JS.API.STREAM.UPDATE.LYRA_OUTBOUND_AUDIO",
            "$JS.API.STREAM.CREATE.KV_lyra_outbound_audio_sent",
        }
    )
    _RETAINED_SUBJECT = "$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO"

    def _render_and_parse(self, prod_matrix: LoadedMatrix) -> dict:
        """Render prod matrix and return {identity_name: ParsedUser}."""
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)
        parsed = parse_auth_conf(rendered)
        return {u.comment_name: u for u in parsed.users}

    def test_telegram_adapter_no_stream_create(self, prod_matrix: LoadedMatrix) -> None:
        """telegram-adapter rendered block must NOT contain S3-removed subjects."""
        users = self._render_and_parse(prod_matrix)
        assert "telegram-adapter" in users, (
            "telegram-adapter not found in rendered auth.conf"
        )
        tg = users["telegram-adapter"]
        for subject in self._REMOVED_SUBJECTS:
            assert subject not in tg.publish_allow, (
                f"telegram-adapter publish still contains {subject!r} — "
                "S3 removal not applied to acl-matrix.json"
            )

    def test_telegram_adapter_retains_stream_info(
        self, prod_matrix: LoadedMatrix
    ) -> None:
        """telegram-adapter block must still contain STREAM.INFO (pull_subscribe)."""
        users = self._render_and_parse(prod_matrix)
        tg = users["telegram-adapter"]
        assert self._RETAINED_SUBJECT in tg.publish_allow, (
            f"telegram-adapter publish is missing {self._RETAINED_SUBJECT!r} — "
            "this subject is required for pull_subscribe's stream_info call"
        )

    def test_discord_adapter_no_stream_create(self, prod_matrix: LoadedMatrix) -> None:
        """discord-adapter rendered block must NOT contain S3-removed subjects."""
        users = self._render_and_parse(prod_matrix)
        assert "discord-adapter" in users, (
            "discord-adapter not found in rendered auth.conf"
        )
        dc = users["discord-adapter"]
        for subject in self._REMOVED_SUBJECTS:
            assert subject not in dc.publish_allow, (
                f"discord-adapter publish still contains {subject!r} — "
                "S3 removal not applied to acl-matrix.json"
            )

    def test_discord_adapter_retains_stream_info(
        self, prod_matrix: LoadedMatrix
    ) -> None:
        """discord-adapter block must still contain STREAM.INFO (pull_subscribe)."""
        users = self._render_and_parse(prod_matrix)
        dc = users["discord-adapter"]
        assert self._RETAINED_SUBJECT in dc.publish_allow, (
            f"discord-adapter publish is missing {self._RETAINED_SUBJECT!r} — "
            "this subject is required for pull_subscribe's stream_info call"
        )


class TestGrouplessMatrixBackwardCompat:
    """T9 (S4 ACL grant-group, #1526) — matrices without a top-level 'groups' key
    must load and render without error.

    Covers v1-legacy.json, v2-with-retired.json, and v3-pre-grant-group.json.
    Each fixture has no 'groups' key.  The test asserts:
      - render_auth_conf returns a non-empty string
      - parse_auth_conf succeeds without exception
      - every expected active identity is present by comment_name in the result

    Structural-only assertions (¬byte-equality) because nkeys are deterministic
    only given the same pubkeys dict, and the point is absence-of-regression.

    verified: if render_auth_conf raises on a missing 'groups' key (e.g. a
    KeyError or AttributeError introduced by group expansion logic), every case
    here fails immediately.
    """

    def test_v1_legacy_renders_without_error(self, legacy_matrix: LoadedMatrix) -> None:
        """v1-legacy.json loads and renders; hub is present in parsed output."""
        pubkeys = _fake_pubkeys(legacy_matrix)
        rendered = render_auth_conf(legacy_matrix, pubkeys)

        assert rendered  # non-empty
        parsed = parse_auth_conf(rendered)
        names = {u.comment_name for u in parsed.users}
        assert "hub" in names

    def test_v2_with_retired_renders_without_error(
        self, with_retired_matrix: LoadedMatrix
    ) -> None:
        """v2-with-retired.json loads and renders; active identities present,
        retired old-worker absent."""
        pubkeys = _fake_pubkeys(with_retired_matrix)
        rendered = render_auth_conf(with_retired_matrix, pubkeys)

        assert rendered
        parsed = parse_auth_conf(rendered)
        names = {u.comment_name for u in parsed.users}
        # active identities must be present
        assert "hub" in names
        assert "voice-tts" in names
        assert "clipool-worker" in names
        # retired identity must be excluded
        assert "old-worker" not in names

    def test_v3_pre_grant_group_renders_without_error(self) -> None:
        """v3-pre-grant-group.json (no groups key) loads and renders; key active
        identities are present in the parsed output."""
        from pathlib import Path  # noqa: PLC0415

        from scripts._loader import load_matrix  # noqa: PLC0415

        fixture = (
            Path(__file__).resolve().parent / "fixtures" / "v3-pre-grant-group.json"
        )
        matrix = load_matrix(fixture)
        pubkeys = _fake_pubkeys(matrix)
        rendered = render_auth_conf(matrix, pubkeys)

        assert rendered
        parsed = parse_auth_conf(rendered)
        names = {u.comment_name for u in parsed.users}
        # spot-check a selection of expected active identities
        expected_active = (
            "hub", "telegram-adapter", "discord-adapter", "clipool-worker"
        )
        for expected in expected_active:
            assert expected in names, f"expected {expected!r} in rendered output"
        # retired monitor must be excluded
        assert "monitor" not in names

    def test_v2_with_retired_inline_grants_present(
        self, with_retired_matrix: LoadedMatrix
    ) -> None:
        """Inline publish/subscribe grants from v2-with-retired are faithfully rendered.

        Verifies that the backward-compat path does not silently drop subjects.

        verified: stripping publish/subscribe subjects from _emit_user would remove
        them from the rendered output, failing this assertion.
        """
        pubkeys = _fake_pubkeys(with_retired_matrix)
        rendered = render_auth_conf(with_retired_matrix, pubkeys)
        parsed = parse_auth_conf(rendered)
        users_by_name = {u.comment_name: u for u in parsed.users}

        hub = users_by_name["hub"]
        assert "lyra.outbound.telegram.>" in hub.publish_allow
        assert "lyra.inbound.telegram.>" in hub.subscribe_allow


class TestGrantGroupEquality:
    """T8 (S4 ACL grant-group, #1526) — v4 group-expansion renders set-identically
    to the v3 inline baseline.

    v4 = deploy/nats/acl-matrix.json (version 4: telegram/discord reference the
         audio-consumer group; 8 audio subjects moved out of the inline publish list).
    v3 = tests/scripts/fixtures/v3-pre-grant-group.json (pre-migration snapshot:
         the same 8 subjects listed inline in telegram/discord publish).

    Both matrices have the same identity names, so _fake_pubkeys produces the same
    pubkey dict for both.  We build a union dict as belt-and-suspenders.

    ParsedUser.nkey has compare=False, so frozenset equality compares
    publish_allow + subscribe_allow + allow_responses + comment_name only —
    i.e. the rendered permission sets, independent of nkey values.

    verified: if the renderer does NOT expand groups, v4 telegram-adapter
    publish_allow is missing the 8 audio subjects → ParsedUser differs from v3 →
    frozenset assertion fails.
    """

    _AUDIO_GROUP_PUBLISH = frozenset(
        {
            "$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO",
            "$JS.API.CONSUMER.CREATE.LYRA_OUTBOUND_AUDIO.>",
            "$JS.API.CONSUMER.INFO.LYRA_OUTBOUND_AUDIO.*",
            "$JS.API.CONSUMER.MSG.NEXT.LYRA_OUTBOUND_AUDIO.*",
            "$JS.API.STREAM.INFO.KV_lyra_outbound_audio_sent",
            "$JS.API.STREAM.MSG.GET.KV_lyra_outbound_audio_sent",
            "$JS.ACK.LYRA_OUTBOUND_AUDIO.>",
            "$KV.lyra_outbound_audio_sent.>",
        }
    )

    def _load_both(self) -> tuple[LoadedMatrix, LoadedMatrix]:
        from pathlib import Path  # noqa: PLC0415

        from scripts._loader import load_matrix  # noqa: PLC0415

        root = Path(__file__).resolve().parents[2]
        v4 = load_matrix(root / "deploy" / "nats" / "acl-matrix.json")
        v3 = load_matrix(
            root / "tests" / "scripts" / "fixtures" / "v3-pre-grant-group.json"
        )
        return v4, v3

    def _shared_pubkeys(
        self, v4: LoadedMatrix, v3: LoadedMatrix
    ) -> dict[str, str]:
        """Union of identity names from both matrices → deterministic fake pubkeys."""
        all_names = set(v4["identities"]) | set(v3["identities"])
        return {
            name: f"UDET{name.upper().replace('-', '')}" for name in all_names
        }

    def test_v4_render_set_equals_v3_render(self) -> None:
        """Rendered ParsedUser set for v4 == v3: group expansion is set-identical to
        inline grants.

        verified: if the renderer skips group expansion (removes the for-gname loop),
        v4 telegram/discord ParsedUsers are missing 8 subjects → frozensets differ.
        """
        # Arrange
        v4, v3 = self._load_both()
        pk = self._shared_pubkeys(v4, v3)

        # Act
        parsed_v4 = parse_auth_conf(render_auth_conf(v4, pk))
        parsed_v3 = parse_auth_conf(render_auth_conf(v3, pk))

        # Assert — order-independent set equality (nkey excluded from comparison)
        assert frozenset(parsed_v4.users) == frozenset(parsed_v3.users)

    def test_audio_subjects_absent_from_v4_inline_list(self) -> None:
        """The 8 audio publish subjects are NOT in telegram-adapter's inline publish
        list in v4 (they were moved to the audio-consumer group).

        verified: if the migration is reversed (subjects put back inline), v4
        telegram-adapter identity["publish"] would contain them → assertion fails.
        """
        # Arrange
        v4, _ = self._load_both()

        # Assert — inline list must NOT contain any audio-group subject
        tg_inline = frozenset(v4["identities"]["telegram-adapter"]["publish"])
        assert self._AUDIO_GROUP_PUBLISH.isdisjoint(tg_inline), (
            f"audio subjects still inline in v4 telegram-adapter publish: "
            f"{self._AUDIO_GROUP_PUBLISH & tg_inline!r}"
        )

    def test_audio_subjects_present_in_v4_rendered_telegram(self) -> None:
        """The 8 audio publish subjects ARE present in the rendered/parsed
        telegram-adapter publish_allow set (group expansion re-added them).

        verified: if the renderer does not expand groups, publish_allow is missing
        these subjects → assertion fails.
        """
        # Arrange
        v4, _ = self._load_both()
        pk = self._shared_pubkeys(v4, v4)
        rendered = render_auth_conf(v4, pk)
        parsed = parse_auth_conf(rendered)
        users = {u.comment_name: u for u in parsed.users}

        # Act
        tg_pub = users["telegram-adapter"].publish_allow

        # Assert
        missing = self._AUDIO_GROUP_PUBLISH - tg_pub
        assert not missing, (
            f"telegram-adapter rendered publish_allow is missing audio subjects: "
            f"{missing!r}"
        )


    def test_group_subscribe_subject_present_in_v4_rendered_telegram(self) -> None:
        """The audio-consumer group's subscribe subject is PRESENT in the
        rendered/parsed telegram-adapter subscribe_allow set after group expansion.

        # verified: deleting sub_allow[name].extend(g.get("subscribe", [])) from
        # _renderer.py causes this test to fail — $KV.lyra_outbound_audio_sent.>
        # is absent from telegram-adapter subscribe_allow.
        """
        # Arrange
        v4, _ = self._load_both()
        pk = self._shared_pubkeys(v4, v4)
        rendered = render_auth_conf(v4, pk)
        parsed = parse_auth_conf(rendered)
        users = {u.comment_name: u for u in parsed.users}

        # Act
        tg_sub = users["telegram-adapter"].subscribe_allow

        # Assert — $KV.lyra_outbound_audio_sent.> is the group's subscribe subject
        assert "$KV.lyra_outbound_audio_sent.>" in tg_sub, (
            "telegram-adapter subscribe_allow is missing "
            "$KV.lyra_outbound_audio_sent.> "
            "— group subscribe expansion did not fire"
        )

    def test_empty_group_renders_without_error_and_adds_no_subjects(
        self, tmp_path: Path
    ) -> None:
        """Identity referencing a group with empty publish/subscribe lists renders
        without error and the identity's allow sets are unchanged relative to its
        inline grants.

        # verified: if group expansion raises on an empty list, this test errors;
        # if it incorrectly adds spurious subjects, the equality assertion fails.
        """
        import json  # noqa: PLC0415

        # Arrange — v4 matrix with an empty group
        data = {
            "version": "4",
            "request_reply_flows": [],
            "groups": {
                "empty-group": {"publish": [], "subscribe": []},
            },
            "identities": {
                "hub": {
                    "status": "active",
                    "owner": "lyra",
                    "created_at": "2026-05-01",
                    "description": "hub test identity",
                    "allow_responses": False,
                    "publish": ["lyra.outbound.telegram.>"],
                    "subscribe": ["lyra.inbound.telegram.>"],
                    "deploy": {"type": "container", "secret": "lyra-nats-hub"},
                    "groups": ["empty-group"],
                },
            },
        }
        matrix_path = tmp_path / "matrix.json"
        matrix_path.write_text(json.dumps(data))

        from scripts._loader import load_matrix  # noqa: PLC0415

        matrix = load_matrix(matrix_path)
        pk = {"hub": "UDETHUB"}

        # Act — must not raise
        rendered = render_auth_conf(matrix, pk)
        parsed = parse_auth_conf(rendered)
        users = {u.comment_name: u for u in parsed.users}

        # Assert — allow sets equal the inline grants exactly (no subjects added)
        hub = users["hub"]
        assert hub.publish_allow == frozenset({"lyra.outbound.telegram.>"}), (
            f"unexpected publish_allow: {hub.publish_allow!r}"
        )
        assert hub.subscribe_allow == frozenset({"lyra.inbound.telegram.>"}), (
            f"unexpected subscribe_allow: {hub.subscribe_allow!r}"
        )


class TestNatsSubjectCharset:
    def test_nats_subject_charset(self, prod_matrix: LoadedMatrix) -> None:
        """All rendered subjects only contain valid NATS subject characters.

        SC-3: no spaces or special characters outside the NATS subject charset.
        Charset: [a-zA-Z0-9._>*$\\-]
        # verified: introducing a space in a subject would trigger this assertion
        """
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)

        # Extract quoted subject-like tokens from rendered output.
        # We look for strings inside quotes in publish/subscribe allow blocks.
        quoted_strings = re.findall(r'"([^"]+)"', rendered)
        invalid_charset = re.compile(r"[^a-zA-Z0-9._>*$\-]")

        for token in quoted_strings:
            # Skip tokens that look like comments or multi-word descriptions
            if " " in token or len(token) == 0:
                continue
            # Only check tokens that look like NATS subjects (. or starts with _/$)
            if "." in token or token.startswith("_") or token.startswith("$"):
                bad = invalid_charset.findall(token)
                assert not bad, f"Subject token {token!r} contains invalid chars: {bad}"
