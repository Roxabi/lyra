"""CI shell-test for nats-add-identity Shape-C reload behavior (issue #1719).

Supersedes the #1365 restart-fan-out behavior.  auth.conf is now a bind-mount
(not a secret), so there is no `factory-nats-auth` secret create, and NATS
clients are NOT restarted on identity-add — only `systemctl --user reload
factory-nats` (SIGHUP) is issued so the server picks up the new public nkey
from the already-updated bind-mounted auth.conf.

Stubs podman + systemctl via PATH override to assert the reload path is invoked
correctly per the Phase-2 gate (STATE × secret presence).

Coverage:
  (a) STATE=added → seed secret create + reload (no auth secret, no fan-out).
  (b) STATE=noop + secret present → clean no-op (no secret create, no reload).
  (c) STATE=noop + secret missing → seed secret create + reload (receiving-host case).
"""

from __future__ import annotations

import os
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent


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

        # ── stub: systemctl (all services "active", reload logs) ────────────────────
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
# A — STATE=added → seed secret create + reload (no auth secret, no fan-out)
# ═══════════════════════════════════════════════════════════════════════════════


def test_reload_runs_when_state_added() -> None:
    """STATE=added: seed created + factory-nats reloaded; no auth secret, no fan-out."""
    result, podman_lines, systemctl_lines = _run_with_stubs(
        uv_state="added", podman_inspect_ok=False
    )

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}"
    )

    # Per-identity seed secret MUST be created (type=mount, stays present).
    assert any(
        "secret create --replace factory-nats-test-identity" in line
        for line in podman_lines
    ), "podman secret create factory-nats-test-identity not called"

    # factory-nats-auth was removed (bind-mount now) — must NOT appear.
    assert not any("factory-nats-auth" in line for line in podman_lines), (
        "podman must NOT create factory-nats-auth (it is a bind-mount, not a secret)"
    )

    # SIGHUP reload — not restart.
    assert any("reload factory-nats" in line for line in systemctl_lines), (
        "systemctl reload factory-nats not called"
    )

    # 0-fan-out invariant: no client services restarted (#1719 core point).
    assert not any("restart " in line for line in systemctl_lines), (
        "systemctl restart must NOT be called (0-fan-out contract)"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# B — STATE=noop + secret present → clean no-op (no secret create, no reload)
# ═══════════════════════════════════════════════════════════════════════════════


def test_reload_skipped_when_noop_and_secret_present() -> None:
    """STATE=noop ∧ secret present → clean no-op; no secret create, no reload."""
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

    # Clean no-op: no reload either.
    assert not any("reload" in line for line in systemctl_lines), (
        "systemctl reload must NOT be called in no-op case"
    )


# ═══════════════════════════════════════════════════════════════════════════════
# C — STATE=noop + secret missing → seed create + reload (receiving host)
# ═══════════════════════════════════════════════════════════════════════════════


def test_reload_runs_when_noop_but_secret_missing() -> None:
    """STATE=noop ∧ secret missing → catch-up: seed create + reload, no fan-out."""
    result, podman_lines, systemctl_lines = _run_with_stubs(
        uv_state="noop", podman_inspect_ok=False
    )

    assert result.returncode == 0, (
        f"expected exit 0, got {result.returncode}; stderr={result.stderr!r}"
    )

    # Per-identity seed secret MUST be created.
    assert any(
        "secret create --replace factory-nats-test-identity" in line
        for line in podman_lines
    ), "podman secret create factory-nats-test-identity not called"

    # factory-nats-auth is a bind-mount — must NOT be created.
    assert not any("factory-nats-auth" in line for line in podman_lines), (
        "podman must NOT create factory-nats-auth (it is a bind-mount, not a secret)"
    )

    # SIGHUP reload — not restart.
    assert any("reload factory-nats" in line for line in systemctl_lines), (
        "systemctl reload factory-nats not called"
    )

    # 0-fan-out invariant: no client services restarted.
    assert not any("restart " in line for line in systemctl_lines), (
        "systemctl restart must NOT be called (0-fan-out contract)"
    )
