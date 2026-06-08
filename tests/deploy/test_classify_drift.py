"""Tests for the _classify_drift shell function in deploy/lib/deploy-common.sh.

Fingerprint format: <git_head>:<unit_sha>:<auth_sha>:<voicecli_head>
  - field 0 (git_head)    → structural
  - field 1 (unit_sha)    → structural
  - field 2 (auth_sha)    → auth
  - field 3 (voicecli_head) → structural

The function exits 0 in all cases; callers branch on stdout.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY_COMMON = REPO_ROOT / "deploy" / "lib" / "deploy-common.sh"


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


# ---------------------------------------------------------------------------
# Parametrized contract cases
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "last, current, expected",
    [
        # 1. Identical fingerprints → no drift
        (
            "a:b:c:d",
            "a:b:c:d",
            "none",
        ),
        # 2. No prior stamp (sentinel "none") → structural (full converge)
        (
            "none",
            "a:b:c:d",
            "structural",
        ),
        # 3. Only auth field (index 2) differs → auth-only reload
        (
            "a:b:c:d",
            "a:b:X:d",
            "auth",
        ),
        # 4. Field 0 (git_head) differs → structural
        (
            "a:b:c:d",
            "A:b:c:d",
            "structural",
        ),
        # 5. Field 1 (unit_sha) differs → structural
        (
            "a:b:c:d",
            "a:B:c:d",
            "structural",
        ),
        # 6. Field 3 (voicecli_head) differs → structural
        (
            "a:b:c:d",
            "a:b:c:D",
            "structural",
        ),
        # 7. Auth + structural both differ → structural dominates
        (
            "a:b:c:d",
            "A:b:X:d",
            "structural",
        ),
    ],
    ids=[
        "equal_fingerprints→none",
        "no_prior_stamp→structural",
        "only_auth_differs→auth",
        "field0_git_differs→structural",
        "field1_unit_differs→structural",
        "field3_voice_differs→structural",
        "auth_and_structural_both_differ→structural_dominates",
    ],
)
def test_classify_drift(last: str, current: str, expected: str) -> None:
    """_classify_drift returns the correct drift category for each case.

    Non-tautology rationale (per-case guard deletion analysis):
    - equal_fingerprints→none: delete the `last == current` guard → falls through
      to field-split path; all fields equal so emits "auth" (not "none") → RED
    - no_prior_stamp→structural: delete the `last == "none"` guard → tries to
      IFS-split "none" into 4 fields; last_git="none", cur_git="a", they differ →
      still emits "structural" in this case. However the meaningful negative test
      is the guard ordering: if the "none" check is removed and "none" is treated
      as a 1-field fingerprint, the behaviour is implementation-undefined and
      unpredictable. The test still exercises the dedicated early-exit path.
    - only_auth_differs→auth: delete structural check → skips to `echo "auth"` →
      still passes. Delete the `last == current` guard only → reaches field split,
      structural fields all match, emits "auth" → still passes. The meaningful
      negative: if auth field check is inverted to be structural, emits "structural"
      not "auth" → RED.
    - field0/1/3_differs→structural: delete the structural-fields guard (the `if`
      block) → falls through to `echo "auth"` → emits "auth" not "structural" → RED
    - auth_and_structural_both_differ→structural_dominates: same as above — if the
      structural guard is deleted, emits "auth" → RED.
    """
    assert _classify(last, current) == expected
