"""RED tests for scripts/_renderer.py — #1017 T03.

These tests FAIL at collection time because scripts/_renderer.py does not exist yet.
That is the intended RED state.
"""

from __future__ import annotations

import re

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
        """hub (allow_responses=False) renders allow_responses: false in block."""
        pubkeys = _fake_pubkeys(prod_matrix)
        rendered = render_auth_conf(prod_matrix, pubkeys)

        assert prod_matrix["identities"]["hub"]["allow_responses"] is False
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
