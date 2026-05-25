"""Snapshot test for deploy/quadlet/lyra-blobstore.container — H1 (#1362)."""
from __future__ import annotations

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
QUADLET_UNIT = REPO_ROOT / "deploy" / "quadlet" / "lyra-blobstore.container"


def test_exec_start_pre_guards_tailscale_ipv4() -> None:
    """The unit must refuse to start when TAILSCALE_IPV4 is empty/unset.

    Without this guard, an empty/unset TAILSCALE_IPV4 lets Podman resolve
    PublishPort=${TAILSCALE_IPV4}:8449:8449 to 0.0.0.0:8449 (LAN-exposed).
    The ExecStartPre= guard prevents the container from entering running
    state in that condition.
    """
    content = QUADLET_UNIT.read_text(encoding="utf-8")
    exec_start_pre_lines = [
        line for line in content.splitlines() if line.startswith("ExecStartPre=")
    ]
    assert exec_start_pre_lines, "ExecStartPre= directive missing from Quadlet unit"
    assert any("TAILSCALE_IPV4" in line for line in exec_start_pre_lines), (
        "ExecStartPre= must reference TAILSCALE_IPV4 to fail-closed"
    )
