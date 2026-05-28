"""CI shell-test for nats-add-identity Phase-2 restart branch (issue #1365).

Stubs podman + systemctl via PATH override to assert the restart loop
is invoked correctly per the Phase-2 gate (STATE × secret presence).

Coverage:
  (a) STATE=added → Phase 2 always runs (restart + secret create).
  (b) STATE=noop + secret present → Phase 2 skipped (no restart).
  (c) STATE=noop + secret missing → Phase 2 runs (receiving host case).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent

_EXPECTED_RESTART_SVCS = [
    "lyra-nats",
    "lyra-hub",
    "lyra-telegram",
    "lyra-discord",
    "lyra-clipool",
    "lyra-turn-writer",
    "lyra-gh-helper",
]


def _run_with_stubs(
    *,
    uv_state: str,
    podman_inspect_ok: bool,
) -> tuple[subprocess.CompletedProcess[str], list[str], list[str]]:
    """Run `make nats-add-identity` with stubbed uv, podman, systemctl.

    Returns the CompletedProcess plus podman and systemctl invocation log
    lines (read before the temp directory is cleaned up).
    """
    with tempfile.TemporaryDirectory() as tmp:
        stubs = Path(tmp) / "stubs"
        stubs.mkdir()
        podman_log = Path(tmp) / "podman.log"
        systemctl_log = Path(tmp) / "systemctl.log"

        # ── stub: uv (returns requested STATE so Phase 2 gate evaluates predictably) ──
        (stubs / "uv").write_text(
            '#!/bin/sh\necho "STATE=' + uv_state + '"\nexit 0\n',
            encoding="utf-8",
        )
        (stubs / "uv").chmod(0o755)

        # ── stub: podman (logs invocations, secret inspect controllable) ─────────────
        inspect_rc = "0" if podman_inspect_ok else "1"
        (stubs / "podman").write_text(
            "#!/bin/sh\n"
            f'echo "$@" >> "{podman_log}"\n'
            f'if [ "$1" = "secret" ] && [ "$2" = "inspect" ];'
            f" then exit {inspect_rc}; fi\n"
            "exit 0\n",
            encoding="utf-8",
        )
        (stubs / "podman").chmod(0o755)

        # ── stub: systemctl (all services "active", restart logs) ────────────────────
        (stubs / "systemctl").write_text(
            f'#!/bin/sh\necho "$@" >> "{systemctl_log}"\nexit 0\n',
            encoding="utf-8",
        )
        (stubs / "systemctl").chmod(0o755)

        env = {**os.environ, "PATH": f"{stubs}:{os.environ['PATH']}"}
        result = subprocess.run(
            ["make", "nats-add-identity", "NAME=test-identity"],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            env=env,
        )

        podman_lines = (
            podman_log.read_text().splitlines() if podman_log.exists() else []
        )
        systemctl_lines = (
            systemctl_log.read_text().splitlines() if systemctl_log.exists() else []
        )
        return result, podman_lines, systemctl_lines


# ═══════════════════════════════════════════════════════════════════════════════
# A — STATE=added → Phase 2 always runs
# ═══════════════════════════════════════════════════════════════════════════════


def test_restart_runs_when_state_added() -> None:
    """STATE=added forces Phase 2 regardless of secret presence."""
    result, podman_lines, systemctl_lines = _run_with_stubs(
        uv_state="added", podman_inspect_ok=False
    )

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}"
    )

    assert any(
        "secret create --replace lyra-nats-test-identity" in line
        for line in podman_lines
    ), "podman secret create lyra-nats-test-identity not called"
    assert any(
        "secret create --replace lyra-nats-auth" in line for line in podman_lines
    ), "podman secret create lyra-nats-auth not called"

    for svc in _EXPECTED_RESTART_SVCS:
        assert any(f"restart {svc}" in line for line in systemctl_lines), (
            f"systemctl restart {svc} not called"
        )
        assert any(f"is-active --quiet {svc}" in line for line in systemctl_lines), (
            f"systemctl is-active {svc} not called"
        )


# ═══════════════════════════════════════════════════════════════════════════════
# B — STATE=noop + secret present → Phase 2 skipped
# ═══════════════════════════════════════════════════════════════════════════════


def test_restart_skipped_when_noop_and_secret_present() -> None:
    """STATE=noop ∧ secret present → clean no-op; no restart, no secret create."""
    result, podman_lines, systemctl_lines = _run_with_stubs(
        uv_state="noop", podman_inspect_ok=True
    )

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}"
    )
    assert "already provisioned + Podman secret present locally" in result.stdout, (
        "expected no-op message in stdout"
    )

    assert not any("secret create" in line for line in podman_lines), (
        "podman secret create must NOT be called in no-op case"
    )

    assert not any("restart" in line for line in systemctl_lines), (
        "systemctl restart must NOT be called in no-op case"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C — STATE=noop + secret missing → Phase 2 runs (receiving host)
# ═══════════════════════════════════════════════════════════════════════════════


def test_restart_runs_when_noop_but_secret_missing() -> None:
    """STATE=noop ∧ secret missing → receiving-host catch-up; Phase 2 runs."""
    result, podman_lines, systemctl_lines = _run_with_stubs(
        uv_state="noop", podman_inspect_ok=False
    )

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}"
    )

    assert any(
        "secret create --replace lyra-nats-test-identity" in line
        for line in podman_lines
    ), "podman secret create lyra-nats-test-identity not called"
    assert any(
        "secret create --replace lyra-nats-auth" in line for line in podman_lines
    ), "podman secret create lyra-nats-auth not called"

    for svc in _EXPECTED_RESTART_SVCS:
        assert any(f"restart {svc}" in line for line in systemctl_lines), (
            f"systemctl restart {svc} not called"
        )
