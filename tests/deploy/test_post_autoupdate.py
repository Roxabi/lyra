"""CI wrapper for tests/deploy/test_post_autoupdate.sh (shim-based, no podman needed).

The bash suite guards factory-post-autoupdate's contract: no false drift/pull when the
remote index digest is already in local RepoDigests (#1749), pull+converge on real drift,
and the unconditional change-gated converge on the no-drift path (podman-wins race
self-heal — required by the image-carried converge skip). It was previously not wired
into any CI step; this wrapper runs it with the factory test suite.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent / "test_post_autoupdate.sh"


def test_post_autoupdate_contract() -> None:
    """The shim-based bash suite must pass end to end."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True,
        text=True,
        timeout=120,
    )
    assert result.returncode == 0, (
        f"test_post_autoupdate.sh failed (exit {result.returncode}):\n"
        f"{result.stdout}\n{result.stderr}"
    )
