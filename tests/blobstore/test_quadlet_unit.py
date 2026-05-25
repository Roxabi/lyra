"""Snapshot test for deploy/quadlet/lyra-blobstore.container — H1 (#1362)."""
from __future__ import annotations

import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
QUADLET_UNIT = REPO_ROOT / "deploy" / "quadlet" / "lyra-blobstore.container"

# Match the shell expression shape `[ -n "${TAILSCALE_IPV4}" ]` (with optional
# braces / whitespace around the bracket). An inert stub like
# `ExecStartPre=/bin/sh -c 'exit 0' # TAILSCALE_IPV4` (string-only mention)
# fails this pattern — the test is no longer tautological.
_GUARD_PATTERN = re.compile(r'\[\s*-n\s+"\$\{?TAILSCALE_IPV4\}?"\s*\]')


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
    guarded = [line for line in exec_start_pre_lines if _GUARD_PATTERN.search(line)]
    assert guarded, (
        "No ExecStartPre= line carries the `[ -n \"${TAILSCALE_IPV4}\" ]` "
        "shell test — guard is missing or refactored to a tautology"
    )


def test_service_env_file_in_scope_for_exec_start_pre() -> None:
    """`${TAILSCALE_IPV4}` must be in systemd scope when ExecStartPre fires.

    `[Container] EnvironmentFile=` becomes Podman's `--env-file` (container
    scope only); the systemd-level `ExecStartPre=` runs BEFORE the container
    starts, so it needs a `[Service] EnvironmentFile=` declaration to read
    `${TAILSCALE_IPV4}` from the env file. Without this, the guard always
    fires (empty var → exit 1) and the service is permanently broken.
    """
    content = QUADLET_UNIT.read_text(encoding="utf-8")
    # Split into sections by header lines like [Service], [Container], etc.
    in_service = False
    service_lines: list[str] = []
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[") and stripped.endswith("]"):
            in_service = stripped == "[Service]"
            continue
        if in_service:
            service_lines.append(line)
    assert any(
        line.strip().startswith("EnvironmentFile=") for line in service_lines
    ), (
        "[Service] section must declare EnvironmentFile= so ${TAILSCALE_IPV4} "
        "resolves in the ExecStartPre= systemd scope"
    )
