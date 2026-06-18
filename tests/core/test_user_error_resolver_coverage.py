"""ADR-089 CI gate — every llm.* and cli.parse KNOWN_CODE is resolver-covered."""

from __future__ import annotations

from factory.core.messaging.utils import user_error_resolver as resolver
from roxabi_contracts.errors import KNOWN_CODES


def _is_covered(code: str) -> bool:
    if code in resolver._CODE_TO_TEMPLATE:
        return True
    if code in resolver._PASSTHROUGH_CODES:
        return True
    if code in resolver._GENERIC_ONLY_CODES:
        return True
    return resolver._is_generic_code(code)


class TestUserErrorResolverKnownCodesCoverage:
    def test_llm_and_cli_parse_codes_are_resolver_covered(self) -> None:
        targets = sorted(
            code
            for code in KNOWN_CODES
            if code.startswith("llm.") or code == "cli.parse"
        )
        assert targets, "expected at least one llm.* / cli.parse code in KNOWN_CODES"
        uncovered = [code for code in targets if not _is_covered(code)]
        assert uncovered == [], f"uncovered codes: {uncovered}"