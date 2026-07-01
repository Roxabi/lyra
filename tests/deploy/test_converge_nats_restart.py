"""Tests for deploy/converge.sh restart branches (auth vs structural drift).

Exercises _do_converge via a hermetic BASH_ENV + PATH-stub harness that controls:
  - the convergence fingerprint (via compute_convergence_state override in BASH_ENV)
  - systemctl invocations (via stub executable logging to a file)
  - all other external commands (git, make, factory-acl, seq, sleep, sha256sum, awk, find)

Design: converge.sh is run DIRECTLY (`bash deploy/converge.sh`) so that
`$0` resolves to the real converge.sh path and `source "$(dirname "$0")/..."` works.
Function overrides are injected via `BASH_ENV` (sourced by bash before the script body).
The BASH_ENV file wraps the `source` builtin so overrides survive deploy-common.sh's
own self-source (converge.sh line 9 re-sources deploy-common.sh, which would otherwise
overwrite our overrides).

Assertions:
  (auth)       auth-drift   → restart factory-nats ONLY; no client restarts
  (structural) structural-drift → restart factory-nats + all 7 clients
  (structural) voicecli branch → skipped when VOICE_DIR/.git does not exist

converge runs `systemctl --user daemon-reload` once (steps 3-4 install Quadlet content
that must be picked up before the restarts); it never uses the unit-scoped
`systemctl --user reload factory-nats` SIGHUP path — that lives only in
`make nats-add-identity`.
"""

from __future__ import annotations

import os
import stat
import subprocess
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).parent.parent.parent
CONVERGE_SH = REPO_ROOT / "deploy" / "converge.sh"

# All 7 factory clients restarted on structural drift (per converge.sh lines 86-89)
STRUCTURAL_CLIENTS = [
    "factory-hub",
    "factory-telegram",
    "factory-discord",
    "factory-dashboard",
    "factory-clipool",
    "factory-turn-writer",
    "factory-gh-helper",
    "factory-blobstore",
]


def _make_stub(stubs: Path, name: str, script: str) -> None:
    """Write a stub executable to the stubs directory."""
    p = stubs / name
    p.write_text(script, encoding="utf-8")
    p.chmod(
        stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
    )


def _run_converge(
    *,
    last_fingerprint: str,
    current_fingerprint: str,
    voice_dir_exists: bool = False,
) -> tuple[subprocess.CompletedProcess[str], list[str]]:
    """Run converge.sh directly via ``bash deploy/converge.sh`` in a hermetic env.

    Parameters
    ----------
    last_fingerprint:
        Written to the converge stamp file so that ``read_convergence_state``
        returns it as the "last" value seen by ``_classify_drift``.
    current_fingerprint:
        Returned by the overridden ``compute_convergence_state`` function,
        representing the "current" system state.
    voice_dir_exists:
        If True, creates ``VOICE_DIR/.git`` so the voicecli branch fires.

    Returns
    -------
    (CompletedProcess, systemctl_log_lines)
    """
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp)
        stubs = tmp_path / "stubs"
        stubs.mkdir()
        systemctl_log = tmp_path / "systemctl.log"

        # Fake HOME so ~/.roxabi/factory/ is isolated from the real system
        fake_home = tmp_path / "home"
        fake_home.mkdir()
        factory_data = fake_home / ".roxabi" / "factory"
        factory_data.mkdir(parents=True)

        # Write stamp = last_fingerprint so read_convergence_state returns it
        stamp_path = factory_data / ".converge-stamp"
        stamp_path.write_text(last_fingerprint, encoding="utf-8")

        # FACTORY_DIR is set by deploy-common.sh to "${HOME}/projects/roxabi-factory".
        # converge.sh line 30 does `(cd "${FACTORY_DIR}" && git pull ...)` — the dir
        # must exist even though git is stubbed to exit 0.
        projects_dir = fake_home / "projects"
        projects_dir.mkdir(parents=True)
        (projects_dir / "roxabi-factory").mkdir(parents=True)
        # converge.sh calls PROJECTS_DIR/deploy.sh --prune (role-aware quadlet install).
        deploy_sh = projects_dir / "deploy.sh"
        deploy_sh.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        deploy_sh.chmod(
            stat.S_IRWXU | stat.S_IRGRP | stat.S_IXGRP | stat.S_IROTH | stat.S_IXOTH
        )

        # VOICE_DIR: point to a non-existent dir by default (voicecli branch skipped)
        voice_dir = fake_home / "projects" / "voiceCLI"
        if voice_dir_exists:
            (voice_dir / ".git").mkdir(parents=True)

        # ── Stub executables ─────────────────────────────────────────────────
        # systemctl: log all invocations; is-active → 0 (unit is active);
        #            is-failed → 1 (unit is NOT in failed state)
        _make_stub(
            stubs,
            "systemctl",
            f'#!/bin/sh\necho "$@" >> "{systemctl_log}"\n'
            "case \"$*\" in\n"
            "  *is-failed*) exit 1;;\n"
            "  *) exit 0;;\n"
            "esac\n",
        )

        # git: stub for git pull and git rev-parse (compute_convergence_state is
        # overridden so git is only called for `git pull --ff-only`)
        _make_stub(stubs, "git", "#!/bin/sh\nexit 0\n")

        # make: stub for `make -C ... quadlet-install NO_RESTART=1`
        _make_stub(stubs, "make", "#!/bin/sh\nexit 0\n")

        # factory-acl: stub for `factory-acl genkeys --regen-authconf`
        _make_stub(stubs, "factory-acl", "#!/bin/sh\nexit 0\n")

        # sleep: no-op so the is-active poll loop (seq 1 30) finishes instantly
        _make_stub(stubs, "sleep", "#!/bin/sh\nexit 0\n")

        # seq: produce exactly one iteration so the poll loop terminates
        _make_stub(stubs, "seq", "#!/bin/sh\nprintf '1\\n'\nexit 0\n")

        # find: used by compute_convergence_state (overridden), stub for safety
        _make_stub(stubs, "find", "#!/bin/sh\nexit 0\n")

        # sha256sum: used by compute_convergence_state (overridden), stub for safety
        _make_stub(stubs, "sha256sum", '#!/bin/sh\necho "abc123  -"\nexit 0\n')

        # awk: used by _classify_drift field-count guard and compute_convergence_state.
        # _classify_drift uses: awk -F: '{print NF}' <<< "${fingerprint}"
        # We let awk run from the real PATH (it is a pure text utility with no side
        # effects on the filesystem); NOT stubbed.

        # bash: used for `bash "${FACTORY_DIR}/deploy/install.sh" --secrets-only`
        # Intercept install.sh invocations and no-op them; let other bash calls through.
        _make_stub(
            stubs,
            "bash",
            "#!/bin/sh\n"
            'for arg in "$@"; do\n'
            '  case "${arg}" in *install.sh) exit 0;; esac\n'
            "done\n"
            'exec /bin/bash "$@"\n',
        )

        # ── BASH_ENV override file ───────────────────────────────────────────
        # Sourced by bash BEFORE converge.sh body runs.  Defines:
        #   _apply_overrides()  — redefines the 3 functions we need to control
        #   source()            — wraps builtin source; re-applies overrides after
        #                         every source call (survives deploy-common.sh re-source)
        # We inject current_fingerprint as a literal string in the heredoc so the
        # override function carries the value without needing a variable export.
        bash_env = tmp_path / "bash_env.sh"
        bash_env.write_text(
            "# BASH_ENV: injected before converge.sh body; overrides survive re-source.\n"
            "\n"
            "_apply_overrides() {\n"
            f'    compute_convergence_state() {{ echo "{current_fingerprint}"; }}\n'
            "    write_convergence_state() { :; }\n"
            '    with_deploy_lock() { "$@"; }\n'
            "    require_host_role() { :; }\n"
            "}\n"
            "\n"
            "# Wrap the source builtin: re-apply overrides after every source call so\n"
            "# that deploy-common.sh sourcing (converge.sh line 9) does not clobber them.\n"
            "source() {\n"
            '    builtin source "$@"\n'
            "    _apply_overrides\n"
            "}\n"
            "\n"
            "# Apply immediately for code that runs before the first source call.\n"
            "_apply_overrides\n",
            encoding="utf-8",
        )

        env = {
            **os.environ,
            "PATH": f"{stubs}:{os.environ['PATH']}",
            "HOME": str(fake_home),
            "XDG_RUNTIME_DIR": os.environ.get(
                "XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}"
            ),
            # VOICE_DIR: must be set so converge.sh uses our fake path, not HOME-relative
            "VOICE_DIR": str(voice_dir),
            # BASH_ENV is sourced by bash before any non-interactive script
            "BASH_ENV": str(bash_env),
        }

        # Run converge.sh DIRECTLY (not sourced from a wrapper) so that
        # `$(dirname "$0")` in converge.sh line 9 resolves to the real deploy/ dir.
        result = subprocess.run(
            ["/bin/bash", str(CONVERGE_SH)],
            capture_output=True,
            text=True,
            env=env,
            timeout=30,
        )

        systemctl_lines = (
            systemctl_log.read_text(encoding="utf-8").splitlines()
            if systemctl_log.exists()
            else []
        )
        return result, systemctl_lines


# ─────────────────────────────────────────────────────────────────────────────
# Fingerprint helpers
# fingerprint format: <git_head>:<unit_sha>:<auth_sha>:<voicecli_head>
# Fields 0,1,3 = structural; field 2 = auth.
# ─────────────────────────────────────────────────────────────────────────────


def _auth_drift_fingerprints() -> tuple[str, str]:
    """Return (last, current) where only auth_sha (field 2) differs → auth drift."""
    last = "git111:unit222:auth_OLD:voice444"
    current = "git111:unit222:auth_NEW:voice444"
    return last, current


def _structural_drift_fingerprints() -> tuple[str, str]:
    """Return (last, current) where git_head (field 0) differs → structural drift."""
    last = "git_OLD:unit222:auth333:voice444"
    current = "git_NEW:unit222:auth333:voice444"
    return last, current


# ═════════════════════════════════════════════════════════════════════════════
# A — auth drift → restart factory-nats ONLY; no client fan-out; no reload
# ═════════════════════════════════════════════════════════════════════════════


class TestAuthDrift:
    """auth-only drift: converge restarts factory-nats only (clients reconnect via allow_reconnect)."""

    def _run(self) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        last, current = _auth_drift_fingerprints()
        return _run_converge(last_fingerprint=last, current_fingerprint=current)

    def test_exits_zero(self) -> None:
        """auth drift converge exits 0."""
        result, _ = self._run()
        assert result.returncode == 0, (
            f"converge exited {result.returncode}; "
            f"stderr={result.stderr!r}; stdout={result.stdout!r}"
        )

    def test_factory_nats_restarted(self) -> None:
        """auth drift: factory-nats is restarted (converge.sh line 77: _restart_nats)."""
        _, lines = self._run()
        assert any("restart factory-nats" in line for line in lines), (
            f"expected 'restart factory-nats' in systemctl log; got: {lines!r}"
        )

    def test_no_client_restarts(self) -> None:
        """auth drift: no factory client services are restarted (0-fan-out).

        Non-tautology: if the auth branch is deleted and converge falls through to
        the structural else-block, the 7-client restart loop runs and this
        assertion fails → RED.
        """
        _, lines = self._run()
        for client in STRUCTURAL_CLIENTS:
            assert not any(f"restart {client}" in line for line in lines), (
                f"auth drift must NOT restart {client}; "
                f"systemctl log: {lines!r}"
            )

    def test_daemon_reload_called(self) -> None:
        """converge runs `systemctl --user daemon-reload` once, right after step 4's
        `make quadlet-install NO_RESTART=1` (which itself skips the reload), so the
        restart(s) below pick up Quadlet unit content installed/rendered in steps 3-4
        instead of relaunching from a stale generated unit.

        Non-tautology: if the daemon-reload were removed from converge.sh, this
        assertion would fail → RED.
        """
        _, lines = self._run()
        assert any("daemon-reload" in line for line in lines), (
            f"expected 'daemon-reload' in systemctl log; got: {lines!r}"
        )

    def test_no_unit_scoped_reload(self) -> None:
        """converge never calls the unit-scoped ACL SIGHUP reload
        (`systemctl --user reload factory-nats`) — that lives only in
        `make nats-add-identity`.

        Non-tautology: if a `systemctl --user reload factory-nats` call were added,
        this assertion would fail → RED.
        """
        _, lines = self._run()
        assert not any("reload factory-nats" in line for line in lines), (
            f"converge must not call unit-scoped reload; got: {lines!r}"
        )


# ═════════════════════════════════════════════════════════════════════════════
# B — structural drift → restart factory-nats + all 7 clients
# ═════════════════════════════════════════════════════════════════════════════


class TestStructuralDrift:
    """structural drift: converge restarts factory-nats + all 7 factory clients."""

    def _run(self) -> tuple[subprocess.CompletedProcess[str], list[str]]:
        last, current = _structural_drift_fingerprints()
        return _run_converge(last_fingerprint=last, current_fingerprint=current)

    def test_exits_zero(self) -> None:
        """structural drift converge exits 0."""
        result, _ = self._run()
        assert result.returncode == 0, (
            f"converge exited {result.returncode}; "
            f"stderr={result.stderr!r}; stdout={result.stdout!r}"
        )

    def test_factory_nats_restarted(self) -> None:
        """structural drift: factory-nats is restarted (converge.sh line 81: _restart_nats)."""
        _, lines = self._run()
        assert any("restart factory-nats" in line for line in lines), (
            f"expected 'restart factory-nats' in systemctl log; got: {lines!r}"
        )

    def test_all_clients_restarted(self) -> None:
        """structural drift: all 7 factory clients are restarted (converge.sh lines 86-89).

        Non-tautology: if the structural else-block is deleted (only auth branch
        remains), the 7-client restart loop never runs and this assertion fails → RED.
        """
        _, lines = self._run()
        for client in STRUCTURAL_CLIENTS:
            assert any(f"restart {client}" in line for line in lines), (
                f"structural drift must restart {client}; "
                f"systemctl log: {lines!r}"
            )

    def test_daemon_reload_called(self) -> None:
        """converge runs `systemctl --user daemon-reload` once, right after step 4's
        `make quadlet-install NO_RESTART=1` (which itself skips the reload), so the
        restart(s) below pick up Quadlet unit content installed/rendered in steps 3-4
        instead of relaunching from a stale generated unit.

        Non-tautology: if the daemon-reload were removed from converge.sh, this
        assertion would fail → RED.
        """
        _, lines = self._run()
        assert any("daemon-reload" in line for line in lines), (
            f"expected 'daemon-reload' in systemctl log; got: {lines!r}"
        )

    def test_no_unit_scoped_reload(self) -> None:
        """converge never calls the unit-scoped ACL SIGHUP reload
        (`systemctl --user reload factory-nats`) — that lives only in
        `make nats-add-identity`.

        Non-tautology: if a `systemctl --user reload factory-nats` call were added,
        this assertion would fail → RED.
        """
        _, lines = self._run()
        assert not any("reload factory-nats" in line for line in lines), (
            f"converge must not call unit-scoped reload; got: {lines!r}"
        )

    def test_voicecli_skipped_when_no_git_dir(self) -> None:
        """structural drift: voicecli-tts/stt are NOT restarted when VOICE_DIR/.git absent.

        Non-tautology: if the `if [ -d "${VOICE_DIR}/.git" ]` guard were removed,
        the voicecli restart loop would run unconditionally and this assertion
        would fail → RED.
        """
        _, lines = self._run()
        assert not any("voicecli-tts" in line for line in lines), (
            f"voicecli-tts must not be restarted when VOICE_DIR/.git absent; "
            f"systemctl log: {lines!r}"
        )
        assert not any("voicecli-stt" in line for line in lines), (
            f"voicecli-stt must not be restarted when VOICE_DIR/.git absent; "
            f"systemctl log: {lines!r}"
        )
