"""Snapshot test for deploy/quadlet/lyra-blobstore.container — H1 (#1362)."""

from __future__ import annotations

import pathlib
import re
import subprocess

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
QUADLET_UNIT = REPO_ROOT / "deploy" / "quadlet" / "lyra-blobstore.container"

# Match the whitespace-safe guard shape: tr -d strips whitespace then -n tests
# the residue. An inert stub or the old bare `[ -n "${TAILSCALE_IPV4}" ]` form
# fails this pattern — the test is no longer tautological after #1368.
# Two sub-patterns checked independently on the same line:
#   - tr -d is present (whitespace strip)
#   - [ -n "$v" ] is present (test on stripped residue, not raw var)
_TR_STRIP_PATTERN = re.compile(r"tr\s+-d")
_STRIPPED_TEST_PATTERN = re.compile(r'\[\s*-n\s+"\\?\$v"\s*\]')
# Also verify the variable is still referenced in the guard line.
_VAR_PATTERN = re.compile(r"\$\{?TAILSCALE_IPV4\}?")


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
    # Verify tr-based whitespace strip + stripped-residue test + variable reference
    # are all present on the same ExecStartPre= line.
    guarded = [
        line
        for line in exec_start_pre_lines
        if (
            _TR_STRIP_PATTERN.search(line)
            and _STRIPPED_TEST_PATTERN.search(line)
            and _VAR_PATTERN.search(line)
        )
    ]
    assert guarded, (
        "No ExecStartPre= line carries the whitespace-safe guard "
        '(tr -d + [ -n "$v" ] + ${TAILSCALE_IPV4} reference) — '
        "guard is missing, uses old bare -n form, or refactored to a tautology (#1368)"
    )


def _extract_exec_start_pre_shell(content: str) -> str | None:
    """Return the shell command string from the first ExecStartPre= line, or None."""
    for line in content.splitlines():
        if line.startswith("ExecStartPre="):
            # Strip the directive prefix and any surrounding /bin/sh -c '...' wrapper.
            value = line[len("ExecStartPre=") :]
            # Extract the inner shell script from `/bin/sh -c '<script>'`.
            m = re.search(r"/bin/sh\s+-c\s+'(.+)'", value)
            if m:
                return m.group(1)
    return None


def test_whitespace_only_tailscale_ipv4_rejected() -> None:
    """Whitespace-only TAILSCALE_IPV4 must exit 1 (not pass the guard).

    `[ -n "   " ]` is TRUE in POSIX sh — the old bare -n guard let whitespace
    values through. The tr-based strip must strip first so the test sees an
    empty string and exits 1. Behavioral test: runs the extracted shell command
    via /bin/sh with controlled env vars (#1368).
    """
    content = QUADLET_UNIT.read_text(encoding="utf-8")
    script = _extract_exec_start_pre_shell(content)
    assert script is not None, "Could not extract shell script from ExecStartPre= line"

    # Whitespace-only — must be rejected (exit 1).
    result_ws = subprocess.run(
        ["/bin/sh", "-c", script],
        env={"TAILSCALE_IPV4": "   ", "PATH": "/bin:/usr/bin"},
        capture_output=True,
    )
    assert result_ws.returncode == 1, (
        "Whitespace-only TAILSCALE_IPV4 passed the guard "
        f"(exit {result_ws.returncode}); "
        "expected exit 1 — guard is not whitespace-safe (#1368)"
    )

    # Empty string — must be rejected (exit 1).
    result_empty = subprocess.run(
        ["/bin/sh", "-c", script],
        env={"TAILSCALE_IPV4": "", "PATH": "/bin:/usr/bin"},
        capture_output=True,
    )
    assert result_empty.returncode == 1, (
        f"Empty TAILSCALE_IPV4 passed the guard (exit {result_empty.returncode}); "
        "expected exit 1"
    )

    # Valid IP — must be accepted (exit 0).
    result_valid = subprocess.run(
        ["/bin/sh", "-c", script],
        env={"TAILSCALE_IPV4": "100.64.1.2", "PATH": "/bin:/usr/bin"},
        capture_output=True,
    )
    assert result_valid.returncode == 0, (
        "Valid TAILSCALE_IPV4 was rejected by the guard "
        f"(exit {result_valid.returncode}); expected exit 0"
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
    assert any(line.strip().startswith("EnvironmentFile=") for line in service_lines), (
        "[Service] section must declare EnvironmentFile= so ${TAILSCALE_IPV4} "
        "resolves in the ExecStartPre= systemd scope"
    )
