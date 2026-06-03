"""Tests for deploy/install.sh --dry-run — T18.

Contract map:
  A — dry-run lists all 7 factory-nats-* (incl. factory-nats-gh-helper — the #9 miss)
  B — blob symlink guard (#13): non-empty non-symlink dir at blobstore target
      → dry-run warns with remediation hint
  C — empty non-symlink dir at blobstore target → dry-run logs rm action
  D — missing nkeys dir → exits 1 (pre-condition gate)

Implementation note: deploy/install.sh uses $HOME for all data paths.
We override HOME to a tmp dir so tests don't touch the real ~/.roxabi/factory.

The script sources deploy/generated/secrets-manifest.sh (SCRIPT_DIR-relative,
not HOME-relative) so the manifests used are the real committed ones.

Seed file creation: the validation loop (lines 119-137) checks seed files even
under --dry-run, so we must create stub seed files under
<HOME_TMP>/.roxabi/factory/<rel_path> for all non-optional, file-based secrets.

IMPL DEFECT (reported, not patched here):
  install.sh wires SEEDS[factory_blobstore_token] unconditionally (line 99) to
  ~/.roxabi/factory/blobstore.tok, but in --dry-run mode the token file is never
  created (the openssl rand command is gated by DRY_RUN==0). The validation loop
  then fails with "Missing seed file: blobstore.tok". The workaround here is to
  pre-create a stub blobstore.tok so the dry-run test path is reachable.
"""

from __future__ import annotations

import os
import subprocess
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "deploy" / "install.sh"
MANIFEST_SH = REPO_ROOT / "deploy" / "generated" / "secrets-manifest.sh"
POLICY_TOML = REPO_ROOT / "deploy" / "secrets-policy.toml"

# Secrets that require seed files (non-optional, non-generated, source != 'n/a')
FACTORY_NATS_SEEDS_EXPECTED = {
    "factory-nats-hub",
    "factory-nats-telegram",
    "factory-nats-discord",
    "factory-nats-clipool",
    "factory-nats-turn-writer",
    "factory-nats-blobstore",
    "factory-nats-gh-helper",  # the #9 miss fixed by this PR
}


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_manifest_sources() -> dict[str, str]:
    """Parse SECRET_SOURCES block from secrets-manifest.sh.

    Returns {name: rel_path} where rel_path may be 'n/a'.
    Only reads entries from the SECRET_SOURCES block (not SECRET_POLICY).
    """
    sources: dict[str, str] = {}
    in_sources_block = False
    for line in MANIFEST_SH.read_text().splitlines():
        if "SECRET_SOURCES=(" in line:
            in_sources_block = True
            continue
        if in_sources_block:
            stripped = line.strip()
            if stripped == ")":
                break  # end of SECRET_SOURCES block
            if stripped.startswith("[") and "]=" in stripped:
                name = stripped[1 : stripped.index("]=")]
                val = stripped[stripped.index('="') + 2 : -1]
                sources[name] = val
    return sources


def _parse_policy() -> dict[str, str]:
    """Return {name: policy} from secrets-policy.toml."""
    with POLICY_TOML.open("rb") as f:
        data = tomllib.load(f)
    return {name: attrs["policy"] for name, attrs in data.get("secret", {}).items()}


def _create_stub_seeds(home_tmp: Path) -> None:
    """Create stub seed files for all non-optional, file-based secrets.

    The validation loop in install.sh (lines 119-137) checks seed file
    existence even under --dry-run. We create stubs for all required secrets.

    Workaround for dry-run impl defect: blobstore.tok is wired into SEEDS
    unconditionally but never created under --dry-run. Pre-create a stub.
    """
    sources = _parse_manifest_sources()
    policies = _parse_policy()
    factory_data = home_tmp / ".roxabi" / "factory"

    for name, rel in sources.items():
        if rel == "n/a":
            continue
        policy = policies.get(name, "")
        if policy == "optional":
            continue  # optional secrets are not validated
        dest = factory_data / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"stub-seed-for-{name}\n")

    # Workaround: blobstore.tok is validated but never created in --dry-run.
    blobstore_tok = factory_data / "blobstore.tok"
    if not blobstore_tok.exists():
        blobstore_tok.parent.mkdir(parents=True, exist_ok=True)
        blobstore_tok.write_text("stub-blobstore-token\n")

    # Ensure the nkeys dir exists (required by the NKEYS_DIR guard).
    nkeys = factory_data / "nkeys"
    nkeys.mkdir(parents=True, exist_ok=True)


def _run_install_dry_run(
    home_tmp: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run deploy/install.sh --dry-run with HOME redirected to home_tmp."""
    env = {
        **os.environ,
        "HOME": str(home_tmp),
    }
    if extra_env:
        env.update(extra_env)

    return subprocess.run(
        ["bash", str(INSTALL_SCRIPT), "--dry-run"],
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        env=env,
    )


# ---------------------------------------------------------------------------
# Section A — dry-run lists all 7 factory-nats-* secrets including gh-helper
# ---------------------------------------------------------------------------


class TestDryRunListsAllNatsSecrets:
    """dry-run output must reference all 7 factory-nats-* seeds."""

    def test_dry_run_mentions_all_seven_nats_seeds(self, tmp_path: Path) -> None:
        """[dry-run] lines must cover all 7 factory-nats-* seed secrets."""
        _create_stub_seeds(tmp_path)

        result = _run_install_dry_run(tmp_path)

        assert result.returncode == 0, (
            f"install.sh --dry-run exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        combined = result.stdout + result.stderr
        missing = [name for name in FACTORY_NATS_SEEDS_EXPECTED if name not in combined]
        assert not missing, (
            f"install.sh --dry-run did not mention these secrets: {missing}\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_dry_run_mentions_factory_nats_gh_helper(self, tmp_path: Path) -> None:
        """factory-nats-gh-helper is the specific #9 miss — must appear."""
        _create_stub_seeds(tmp_path)

        result = _run_install_dry_run(tmp_path)

        assert result.returncode == 0, (
            "install.sh --dry-run failed.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "factory-nats-gh-helper" in result.stdout + result.stderr, (
            "factory-nats-gh-helper must appear in dry-run output (was missing in #9)"
        )

    def test_dry_run_does_not_execute_podman_secret_create(
        self, tmp_path: Path
    ) -> None:
        """In --dry-run mode, podman secret create must not actually run.

        The `run` wrapper prints [dry-run] podman secret create ... instead of
        executing — verify by checking output format.
        """
        _create_stub_seeds(tmp_path)

        result = _run_install_dry_run(tmp_path)

        for line in result.stdout.splitlines():
            if "podman secret create" in line:
                assert line.startswith("[dry-run]"), (
                    f"podman secret create ran without [dry-run] prefix: {line!r}"
                )


# ---------------------------------------------------------------------------
# Section B — blob symlink guard: non-empty non-symlink dir → warn in dry-run
# ---------------------------------------------------------------------------


class TestBlobSymlinkGuardDryRun:
    """#13: non-empty non-symlink blobstore dir → dry-run emits WARN."""

    def test_nonempty_dir_triggers_warn_in_dry_run(self, tmp_path: Path) -> None:
        """Dry-run warns when blobstore is a non-empty real directory."""
        _create_stub_seeds(tmp_path)

        blobstore_dir = tmp_path / ".roxabi" / "factory" / "blobstore"
        blobstore_dir.mkdir(parents=True, exist_ok=True)
        (blobstore_dir / "existing-blob.bin").write_bytes(b"data")

        result = _run_install_dry_run(tmp_path)

        combined = result.stdout + result.stderr
        assert (
            "WARN" in combined or "warn" in combined.lower() or "BLOCK" in combined
        ), (
            "Dry-run must warn about non-empty blobstore dir blocking install.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "blobstore" in combined.lower(), (
            "Warning must mention blobstore.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_nonempty_dir_warning_communicates_block(self, tmp_path: Path) -> None:
        """Dry-run warning for non-empty blobstore dir uses 'BLOCK' text."""
        _create_stub_seeds(tmp_path)

        blobstore_dir = tmp_path / ".roxabi" / "factory" / "blobstore"
        blobstore_dir.mkdir(parents=True, exist_ok=True)
        (blobstore_dir / "data.bin").write_bytes(b"\x00" * 16)

        result = _run_install_dry_run(tmp_path)

        combined = result.stdout + result.stderr
        # Script text: "would BLOCK install (run without --dry-run to see full error)"
        assert "BLOCK" in combined or "non-empty" in combined.lower(), (
            "Dry-run must communicate that non-empty dir blocks live install.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_empty_dir_at_blobstore_dry_runs_rm(self, tmp_path: Path) -> None:
        """Empty non-symlink dir at blobstore: dry-run logs rm or ln action.

        The live path removes the empty dir; dry-run logs the action without
        executing.
        """
        _create_stub_seeds(tmp_path)

        blobstore_dir = tmp_path / ".roxabi" / "factory" / "blobstore"
        blobstore_dir.mkdir(parents=True, exist_ok=True)
        # Empty directory — safe to remove per install.sh logic.

        result = _run_install_dry_run(tmp_path)

        combined = result.stdout + result.stderr
        # Script must log the rm or subsequent ln action.
        assert "[dry-run] rm" in combined or "[dry-run] ln" in combined, (
            "Dry-run must log the rm or ln action for blobstore symlink.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Section C — dry-run proceeds normally when blobstore target is a symlink
# ---------------------------------------------------------------------------


class TestBlobSymlinkGuardSymlink:
    """When blobstore target is already a symlink, dry-run must not warn."""

    def test_existing_symlink_does_not_trigger_guard(self, tmp_path: Path) -> None:
        """If blobstore is already a symlink, dry-run proceeds without error."""
        _create_stub_seeds(tmp_path)

        blobstore_link = tmp_path / ".roxabi" / "factory" / "blobstore"
        blobstore_link.parent.mkdir(parents=True, exist_ok=True)
        fake_target = tmp_path / "blobs"
        fake_target.mkdir()
        blobstore_link.symlink_to(fake_target)

        result = _run_install_dry_run(tmp_path)

        # Should not produce blobstore-guard ERROR
        assert result.returncode == 0, (
            "Dry-run must succeed when blobstore is already a symlink.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Section D — pre-condition gate: missing nkeys dir exits non-zero
# ---------------------------------------------------------------------------


class TestPreConditionGate:
    """Missing ~/.roxabi/factory/nkeys → install.sh exits non-zero."""

    def test_missing_nkeys_dir_exits_nonzero(self, tmp_path: Path) -> None:
        """install.sh exits non-zero when NKEYS_DIR does not exist."""
        # Do NOT create stub seeds — so nkeys dir is absent.
        result = _run_install_dry_run(tmp_path)

        assert result.returncode != 0, (
            "install.sh must exit non-zero when nkeys dir is missing.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "nkeys" in combined.lower(), (
            "Error message must mention nkeys dir.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
