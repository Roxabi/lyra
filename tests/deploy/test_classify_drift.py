"""Tests for the _classify_drift shell function in deploy/lib/deploy-common.sh.

Fingerprint format:
  <git_head>:<unit_sha>:<auth_sha>:<voicecli_head>:<staging-svc-digest>:<staging-digest>
  - field 0 (git_head)         → structural
  - field 1 (unit_sha)         → structural
  - field 2 (auth_sha)         → auth
  - field 3 (voicecli_head)    → structural
  - field 4 (staging-svc-digest) → structural
  - field 5 (staging-digest)   → structural

Legacy 4-field stamps are normalized to :none:none before comparison.

The function exits 0 in all cases; callers branch on stdout.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_COMMON = REPO_ROOT / "deploy" / "lib" / "deploy-common.sh"

_FP = "a:b:c:d:img-svc:img-stg"


def _classify(last: str, current: str) -> str:
    """Invoke _classify_drift via bash and return the stripped stdout."""
    result = subprocess.run(
        [
            "bash",
            "-c",
            f'source {DEPLOY_COMMON} && _classify_drift "{last}" "{current}"',
        ],
        capture_output=True,
        text=True,
        env={**os.environ, "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")},
        timeout=10,
    )
    assert result.returncode == 0, (
        f"_classify_drift exited {result.returncode}; stderr={result.stderr!r}"
    )
    return result.stdout.strip()


@pytest.mark.parametrize(
    "last, current, expected",
    [
        # 1. Identical fingerprints → no drift
        (_FP, _FP, "none"),
        # 2. No prior stamp (sentinel "none") → structural (full converge)
        ("none", _FP, "structural"),
        # 3. Only auth field (index 2) differs → auth-only reload
        (_FP, "a:b:X:d:img-svc:img-stg", "auth"),
        # 4. Field 0 (git_head) differs → structural
        (_FP, "A:b:c:d:img-svc:img-stg", "structural"),
        # 5. Field 1 (unit_sha) differs → structural
        (_FP, "a:B:c:d:img-svc:img-stg", "structural"),
        # 6. Field 3 (voicecli_head) differs → structural
        (_FP, "a:b:c:D:img-svc:img-stg", "structural"),
        # 7. Field 4 (staging-svc digest) differs → structural
        (_FP, "a:b:c:d:NEW-svc:img-stg", "structural"),
        # 8. Field 5 (staging digest) differs → structural
        (_FP, "a:b:c:d:img-svc:NEW-stg", "structural"),
        # 9. Auth + structural both differ → structural dominates
        (_FP, "A:b:X:d:img-svc:img-stg", "structural"),
        # 10. None-sentinel guard — non-tautological legacy-normalized case
        (
            "none",
            "none:none:X:none:none:none",
            "structural",
        ),
        # 11. Field-count guard: 7-field fingerprint → structural (fail-safe)
        (_FP, f"{_FP}:extra", "structural"),
        # 12. Legacy 4-field stamp vs 6-field current (image fields added) → structural
        ("a:b:c:d", _FP, "structural"),
        # 13. Legacy equal after normalization (images still none) → none
        ("a:b:c:d", "a:b:c:d:none:none", "none"),
    ],
    ids=[
        "equal_fingerprints→none",
        "no_prior_stamp→structural",
        "only_auth_differs→auth",
        "field0_git_differs→structural",
        "field1_unit_differs→structural",
        "field3_voice_differs→structural",
        "field4_image_svc_differs→structural",
        "field5_image_stg_differs→structural",
        "auth_and_structural_both_differ→structural_dominates",
        "none_sentinel_nontautological→structural",
        "field_count_7_fields→structural",
        "legacy_4field_stamp→structural",
        "legacy_4field_equal_normalized→none",
    ],
)
def test_classify_drift(last: str, current: str, expected: str) -> None:
    """_classify_drift returns the correct drift category for each case."""
    assert _classify(last, current) == expected