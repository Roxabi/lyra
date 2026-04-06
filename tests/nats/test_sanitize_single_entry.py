"""Assert that sanitize_platform_meta() is called exactly once per inbound message.

Issue #525 identified a double-sanitization risk. This test uses the INVENTORY
approach: it explicitly enumerates the expected call sites (grep over source) and
asserts the set matches exactly.  Adding a second call site causes an immediate
failure with a clear diff.

Approach: INVENTORY
  - Walk src/lyra/nats/ and src/lyra/core/hub/ source files
  - Grep for the literal function name ``sanitize_platform_meta``
  - Assert the call-site inventory matches the expected set

Why inventory over dynamic call-count:
  - Static analysis covers async code paths that would need complex setup to
    exercise dynamically
  - Fails loudly with a diff showing the new/unexpected call site
  - Does not require spinning up a NATS server or a live bus

Maintenance contract:
  - If a legitimate new call site is added, update EXPECTED_CALL_SITES below
    together with a comment explaining the architectural justification.
  - Never add a second call site without first updating this test and leaving a
    comment referencing the issue/PR that justifies it.
"""

from __future__ import annotations

import re
from pathlib import Path

# ---------------------------------------------------------------------------
# Expected call sites — update only when architecture intentionally changes
# ---------------------------------------------------------------------------

# Format: "<relative-path-from-repo-root>:<function-or-context>"
# Key is the source file path relative to repo root.
# Value is a short description of the architectural role.
EXPECTED_CALL_SITES: dict[str, str] = {
    # NatsBus._make_handler() sanitizes each inbound message as it arrives from
    # the NATS wire.  This is the primary sanitization point for all hub-inbound
    # paths that flow through NatsBus.
    "src/lyra/nats/nats_bus.py": "NatsBus._make_handler — wire-boundary sanitization",
    # InboundAudioLegacyHandler._convert_legacy() sanitizes legacy InboundAudio
    # payloads arriving via a raw NATS subscription (not NatsBus).  This is a
    # second legitimate wire boundary — the compat shim bypasses NatsBus entirely,
    # so it MUST sanitize before calling Bus.inject().  Added in #534 Slice 1.
    "src/lyra/nats/compat/inbound_audio_legacy.py": (
        "InboundAudioLegacyHandler._convert_legacy — legacy wire-boundary sanitization"
    ),
}

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_SEARCH_ROOTS = [
    Path("src/lyra/nats"),
    Path("src/lyra/core/hub"),
]

_CALL_PATTERN = re.compile(r"\bsanitize_platform_meta\s*\(")
_DEF_PATTERN = re.compile(r"^\s*def\s+sanitize_platform_meta\b")


def _find_call_sites(repo_root: Path) -> dict[str, list[int]]:
    """Return {relative_file_path: [line_numbers]} for all call sites.

    Excludes the function definition itself (only counts invocations).
    """
    results: dict[str, list[int]] = {}
    for root in _SEARCH_ROOTS:
        abs_root = repo_root / root
        for py_file in sorted(abs_root.rglob("*.py")):
            rel = py_file.relative_to(repo_root)
            for lineno, line in enumerate(
                py_file.read_text(encoding="utf-8").splitlines(), start=1
            ):
                if _CALL_PATTERN.search(line) and not _DEF_PATTERN.match(line):
                    results.setdefault(str(rel), []).append(lineno)
    return results


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestSanitizeSingleEntryPoint:
    """Inventory test: sanitize_platform_meta called exactly once in inbound path."""

    def test_sanitize_call_sites_match_expected_inventory(self) -> None:
        # Arrange — locate repo root relative to this test file
        repo_root = Path(__file__).resolve().parent.parent.parent

        # Act — grep for call sites
        call_sites = _find_call_sites(repo_root)

        # Build sets for assertion
        found_files = set(call_sites.keys())
        expected_files = set(EXPECTED_CALL_SITES.keys())

        # Assert — exact match (no more, no fewer)
        unexpected = found_files - expected_files
        missing = expected_files - found_files

        assert not unexpected, (
            f"Unexpected sanitize_platform_meta() call sites found — "
            f"this may indicate duplicate sanitization (issue #525).\n"
            f"  Unexpected: {sorted(unexpected)}\n"
            f"  If this is intentional, add to EXPECTED_CALL_SITES with a "
            f"justification comment."
        )
        assert not missing, (
            f"Expected call site(s) no longer found — inventory is stale.\n"
            f"  Missing: {sorted(missing)}\n"
            f"  Update EXPECTED_CALL_SITES to reflect the new architecture."
        )

    def test_compat_shim_sanitizes_at_wire_boundary(self) -> None:
        """InboundAudioLegacyHandler MUST call sanitize_platform_meta.

        The compat shim uses a raw NATS subscription (not NatsBus), so the
        normal NatsBus._make_handler() sanitization path is bypassed.  The
        shim MUST sanitize before calling Bus.inject() to prevent #525
        regression on the legacy subject tree.
        """
        repo_root = Path(__file__).resolve().parent.parent.parent
        compat_shim = (
            repo_root / "src" / "lyra" / "nats" / "compat" / "inbound_audio_legacy.py"
        )
        assert compat_shim.exists(), f"Compat shim not found: {compat_shim}"
        content = compat_shim.read_text(encoding="utf-8")
        assert _CALL_PATTERN.search(content) is not None, (
            "InboundAudioLegacyHandler does NOT call sanitize_platform_meta() — "
            "this is a #525 regression: legacy audio payloads bypass NatsBus and "
            "must be sanitized in the compat shim before Bus.inject()."
        )

    def test_total_inbound_call_count_matches_expected(self) -> None:
        """Structural cross-check: call count matches expected inventory size."""
        repo_root = Path(__file__).resolve().parent.parent.parent
        call_sites = _find_call_sites(repo_root)
        total = sum(len(lines) for lines in call_sites.values())
        expected = len(EXPECTED_CALL_SITES)
        assert total == expected, (
            f"Expected {expected} sanitize_platform_meta() call(s) in inbound "
            f"source paths, found {total}.\n"
            f"  Call sites: {dict(call_sites)}"
        )
