"""Tests for deploy/install.sh --dry-run — T18.

Contract map:
  A — dry-run lists all factory-nats-* nats-seed secrets from secrets-manifest.sh
  B — blobstore data dir: dry-run logs mkdir of ~/.roxabi/factory/blobstore;
      pre-existing content never blocks
  D — missing nkeys dir → exits 1 (pre-condition gate)

Implementation note: deploy/install.sh uses $HOME for all data paths.
We override HOME to a tmp dir so tests don't touch the real ~/.roxabi/factory.

The script sources deploy/generated/secrets-manifest.sh (SCRIPT_DIR-relative,
not HOME-relative) so the manifests used are the real committed ones.

Seed file creation: the validation loop in install.sh checks seed file existence
under --dry-run for non-optional, file-based secrets.  We create stub files for
all required (non-optional, non-generated, source != 'n/a') secrets.

Generated-policy secrets (factory_blobstore_token) are skipped by the validation
loop when DRY_RUN=1 (install.sh lines ~135-137), so no stub is needed for them.

Optional secrets (factory-gh-pem, factory-claude-oauth) are resolved via the
uniform SECRET_SOURCES map in install.sh; their absence is silently skipped.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
INSTALL_SCRIPT = REPO_ROOT / "deploy" / "install.sh"
MANIFEST_SH = REPO_ROOT / "deploy" / "generated" / "secrets-manifest.sh"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _parse_manifest_block(block_name: str) -> dict[str, str]:
    """Parse a declare -A block from secrets-manifest.sh."""
    entries: dict[str, str] = {}
    in_block = False
    for line in MANIFEST_SH.read_text().splitlines():
        if f"{block_name}=(" in line:
            in_block = True
            continue
        if in_block:
            stripped = line.strip()
            if stripped == ")":
                break
            if stripped.startswith("[") and "]=" in stripped:
                name = stripped[1 : stripped.index("]=")]
                val = stripped[stripped.index('="') + 2 : -1]
                entries[name] = val
    return entries


def _parse_manifest_sources() -> dict[str, str]:
    """Parse SECRET_SOURCES block from secrets-manifest.sh."""
    return _parse_manifest_block("SECRET_SOURCES")


def _parse_manifest_policy() -> dict[str, str]:
    """Parse SECRET_POLICY block from secrets-manifest.sh (install.sh SSoT)."""
    return _parse_manifest_block("SECRET_POLICY")


def _factory_nats_seeds_expected() -> set[str]:
    """All secrets with policy=nats-seed in the committed manifest."""
    return {
        name
        for name, policy in _parse_manifest_policy().items()
        if policy == "nats-seed"
    }


def _create_stub_seeds(home_tmp: Path) -> None:
    """Create stub seed files for all non-optional, file-based secrets.

    The validation loop in install.sh checks seed file existence under --dry-run
    for non-optional, non-generated secrets with a concrete source path.
    Generated-policy secrets are skipped by the loop when DRY_RUN=1.
    Optional secrets are never validated (skipped by policy check).
    """
    sources = _parse_manifest_sources()
    policies = _parse_manifest_policy()
    factory_data = home_tmp / ".roxabi" / "factory"

    for name, rel in sources.items():
        if rel == "n/a":
            continue
        policy = policies.get(name, "")
        if policy in ("optional", "generated"):
            continue  # optional: never validated; generated: skipped under --dry-run
        dest = factory_data / rel
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_text(f"stub-seed-for-{name}\n")

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
# Section A — dry-run lists all factory-nats-* nats-seed secrets
# ---------------------------------------------------------------------------


class TestDryRunListsAllNatsSecrets:
    """dry-run output must reference every factory-nats-* nats-seed secret."""

    def test_dry_run_mentions_all_nats_seed_secrets(self, tmp_path: Path) -> None:
        """[dry-run] lines must cover all manifest nats-seed secrets."""
        _create_stub_seeds(tmp_path)
        expected = _factory_nats_seeds_expected()

        result = _run_install_dry_run(tmp_path)

        assert result.returncode == 0, (
            f"install.sh --dry-run exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

        combined = result.stdout + result.stderr
        missing = [name for name in expected if name not in combined]
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
# Section B — blobstore data dir: mkdir logged; pre-existing content never blocks
# ---------------------------------------------------------------------------


class TestBlobstoreDataDir:
    """§5 creates ~/.roxabi/factory/blobstore as a plain dir; no guard logic."""

    def test_blobstore_dir_is_created(self, tmp_path: Path) -> None:
        """Fresh HOME with no blobstore dir: dry-run logs mkdir for blobstore."""
        _create_stub_seeds(tmp_path)
        # No blobstore dir pre-created — let install.sh handle it.

        result = _run_install_dry_run(tmp_path)

        assert result.returncode == 0, (
            f"install.sh --dry-run exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        combined = result.stdout + result.stderr
        # §5: `run mkdir -p <HOME>/.roxabi/factory/blobstore` → [dry-run] mkdir -p ...
        # and `echo "  [ok]   <HOME>/.roxabi/factory/blobstore"` (always printed)
        # Pin to the §5 path token (.roxabi/factory/blobstore with slash) so the
        # filter excludes unrelated lines like "factory-nats-blobstore already exists"
        # which use a hyphen instead of a slash.
        blobstore_lines = [
            line
            for line in combined.splitlines()
            if ".roxabi/factory/blobstore" in line
            and ("mkdir" in line or "[ok]" in line)
        ]
        assert blobstore_lines, (
            "Dry-run must log a mkdir or [ok] line for the blobstore data dir.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert any(
            "[dry-run] mkdir" in line and ".roxabi/factory/blobstore" in line
            for line in combined.splitlines()
        ), (
            "Dry-run must emit '[dry-run] mkdir ... .roxabi/factory/blobstore'"
            " (§5 mkdir absent).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_preexisting_nonempty_blobstore_does_not_block(
        self, tmp_path: Path
    ) -> None:
        """Pre-existing blobs in blobstore dir must never block dry-run.

        §5 removed the symlink guard entirely.  A real dir with content is
        the expected production state — install must not error or warn on it.
        """
        _create_stub_seeds(tmp_path)

        blobstore_dir = tmp_path / ".roxabi" / "factory" / "blobstore"
        blobstore_dir.mkdir(parents=True, exist_ok=True)
        (blobstore_dir / "existing-blob.bin").write_bytes(b"\x00" * 64)

        result = _run_install_dry_run(tmp_path)

        combined = result.stdout + result.stderr
        assert result.returncode == 0, (
            "Dry-run must exit 0 even when blobstore dir is non-empty.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "BLOCK" not in combined, (
            "Dry-run must NOT emit 'BLOCK' for a non-empty blobstore dir.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert "would BLOCK" not in combined, (
            "Dry-run must NOT emit 'would BLOCK' for a non-empty blobstore dir.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Section C — regression: blobstore Quadlet unit must NOT be explicitly enabled
# ---------------------------------------------------------------------------


class TestBlobstoreServiceNotExplicitlyEnabled:
    """Guard against re-adding `systemctl enable factory-blobstore.service` (#1746).

    Quadlet units are generator-managed: they auto-enable via [Install]
    WantedBy=default.target at daemon-reload, exactly like every other factory-*
    unit. `systemctl --user enable` on a generated unit fails with
    "Unit ... is transient or generated", and under `set -euo pipefail` that
    aborts install.sh before §9 (timer install) and §10 (auto-update enable).

    Note: the dry-run wrapper only echoes commands; it cannot reproduce the live
    failure (the `run` function prints "[dry-run] ..." instead of executing).
    This test guards against the broken line being re-added, not the runtime abort.
    """

    def test_blobstore_service_not_explicitly_enabled(self, tmp_path: Path) -> None:
        """Dry-run must NOT contain `systemctl --user enable factory-blobstore.service`.

        Regression test for #1746: explicit enable on a Quadlet-generated unit
        caused install.sh to abort under set -euo pipefail before §9/§10.
        """
        _create_stub_seeds(tmp_path)

        result = _run_install_dry_run(tmp_path)

        assert result.returncode == 0, (
            f"install.sh --dry-run exited {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        combined = result.stdout + result.stderr
        # The broken line was:
        #   run systemctl --user enable factory-blobstore.service
        # Under --dry-run `run` emits:
        #   [dry-run] systemctl --user enable factory-blobstore.service
        # Match any line that both enables AND names the blobstore unit, so the
        # guard also catches re-introductions that append `--now` (the sibling
        # §10 `enable --now podman-auto-update.timer` makes that variant likely).
        offenders = [
            line
            for line in combined.splitlines()
            if "enable" in line and "factory-blobstore.service" in line
        ]
        assert not offenders, (
            "install.sh must NOT `systemctl --user enable` factory-blobstore.service"
            " (any variant, incl. `--now`).\n"
            "Quadlet units auto-enable via [Install] WantedBy= at daemon-reload;\n"
            "explicit enable fails on generated units and aborts install.sh"
            " before §9/§10 (#1746).\n"
            f"offending lines: {offenders}\n"
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


# ---------------------------------------------------------------------------
# Section E — strict manifest parser regression tests
# ---------------------------------------------------------------------------


def _make_deploy_tree(base: Path) -> tuple[Path, Path]:
    """Create a minimal deploy/ + deploy/generated/ tree in base.

    Returns (install_sh_copy, manifest_path).
    The real install.sh is copied into base/deploy/install.sh.
    The manifest is placed at base/deploy/generated/secrets-manifest.sh.
    SCRIPT_DIR resolves to base/deploy/ so the parser reads our crafted manifest.
    """
    deploy_dir = base / "deploy"
    lib_dir = deploy_dir / "lib"
    lib_dir.mkdir(parents=True)
    for lib_file in (REPO_ROOT / "deploy" / "lib").glob("*.sh"):
        lib_dir.joinpath(lib_file.name).write_bytes(lib_file.read_bytes())
    generated_dir = deploy_dir / "generated"
    generated_dir.mkdir(parents=True)
    install_dst = deploy_dir / "install.sh"
    install_dst.write_bytes(INSTALL_SCRIPT.read_bytes())
    manifest_path = generated_dir / "secrets-manifest.sh"
    return install_dst, manifest_path


def _run_install_from_tree(
    install_sh: Path,
    home_tmp: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:  # type: ignore[type-arg]
    """Run a copy of install.sh from its own deploy/ dir with HOME=home_tmp."""
    env = {**os.environ, "HOME": str(home_tmp)}
    if extra_env:
        env.update(extra_env)
    return subprocess.run(
        ["bash", str(install_sh), "--dry-run"],
        cwd=str(install_sh.parent.parent),  # repo root equivalent
        capture_output=True,
        text=True,
        env=env,
    )


class TestManifestStrictParse:
    """Strict parser security regression tests (replaces `source`)."""

    def test_injected_command_line_is_rejected(self, tmp_path: Path) -> None:
        """A bare shell statement in the manifest must NOT execute and exit non-zero.

        Proof: sentinel file must NOT be created even if the manifest were sourced.
        """
        sentinel = tmp_path / "pwned"
        install_sh, manifest_path = _make_deploy_tree(tmp_path / "repo")
        # Craft a manifest that would execute `touch <sentinel>` if sourced.
        manifest_path.write_text(
            f"""\
# header
declare -A SECRET_SOURCES=(
    [factory-nats-hub]="nkeys/hub.seed"
)
touch {sentinel}
declare -A SECRET_POLICY=(
    [factory-nats-hub]="nats-seed"
)
"""
        )
        home_tmp = tmp_path / "home"
        nkeys = home_tmp / ".roxabi" / "factory" / "nkeys"
        nkeys.mkdir(parents=True)

        result = _run_install_from_tree(install_sh, home_tmp)

        assert result.returncode != 0, (
            "install.sh must exit non-zero on injected command line.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        combined = result.stderr + result.stdout
        assert "refusing" in combined.lower() or "unexpected" in combined.lower(), (
            f"stderr must mention refusing/unexpected line.\nstderr:\n{result.stderr}"
        )
        assert not sentinel.exists(), (
            "Sentinel file was created — injected command executed! Parser is broken."
        )

    def test_value_with_shell_metachars_is_rejected(self, tmp_path: Path) -> None:
        """A data line with $(...) or ; in value must be rejected by charset gate.

        The parser rejects the malformed data line and exits 2 before reaching the
        seed-validation loop — so exit code must be exactly 2, not 1 (missing seed).
        """
        install_sh, manifest_path = _make_deploy_tree(tmp_path / "repo")
        manifest_path.write_text(
            """\
# header
declare -A SECRET_SOURCES=(
    [factory-nats-hub]="nkeys/$(evil)"
)
declare -A SECRET_POLICY=(
    [factory-nats-hub]="nats-seed"
)
"""
        )
        home_tmp = tmp_path / "home"
        nkeys = home_tmp / ".roxabi" / "factory" / "nkeys"
        nkeys.mkdir(parents=True)

        result = _run_install_from_tree(install_sh, home_tmp)

        assert result.returncode == 2, (
            f"install.sh must exit 2 (parse rejection) on value with shell "
            f"metacharacters, got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        combined = result.stderr + result.stdout
        assert "refusing" in combined.lower() or "unexpected" in combined.lower(), (
            "stderr must mention refusing/unexpected on charset rejection.\n"
            f"stderr:\n{result.stderr}"
        )

    def test_legit_manifest_still_parses(self, tmp_path: Path) -> None:
        """The real committed manifest must drive a successful --dry-run.

        Asserts both SECRET_SOURCES and SECRET_POLICY were populated by verifying
        factory-nats-hub appears in the dry-run output.
        """
        _create_stub_seeds(tmp_path)
        result = _run_install_dry_run(tmp_path)

        assert result.returncode == 0, (
            "install.sh --dry-run must succeed with the real committed manifest.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        combined = result.stdout + result.stderr
        assert "factory-nats-hub" in combined, (
            "factory-nats-hub must appear in dry-run output "
            "(proves SECRET_SOURCES + SECRET_POLICY were populated).\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_value_with_dotdot_path_is_rejected(self, tmp_path: Path) -> None:
        """A SECRET_SOURCES value containing '..' must be rejected with exit 2.

        Defense-in-depth: a tampered manifest must not be able to point
        `podman secret create` at an arbitrary operator-readable file via
        path traversal (e.g. ../../etc/shadow).
        """
        install_sh, manifest_path = _make_deploy_tree(tmp_path / "repo")
        manifest_path.write_text(
            """\
# header
declare -A SECRET_SOURCES=(
    [factory-nats-hub]="../../etc/shadow"
)
declare -A SECRET_POLICY=(
    [factory-nats-hub]="nats-seed"
)
"""
        )
        home_tmp = tmp_path / "home"
        nkeys = home_tmp / ".roxabi" / "factory" / "nkeys"
        nkeys.mkdir(parents=True)

        result = _run_install_from_tree(install_sh, home_tmp)

        assert result.returncode == 2, (
            f"install.sh must exit 2 (parse rejection) on '..' value, "
            f"got {result.returncode}.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        combined = result.stderr + result.stdout
        assert "refusing" in combined.lower() or ".." in combined, (
            "stderr must mention refusing or '..' on dotdot rejection.\n"
            f"stderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Section — ingress.toml provisioning (copy-if-absent from the example)
# ---------------------------------------------------------------------------


class TestIngressTomlProvisioning:
    """dry-run provisions ~/.roxabi/factory/ingress.toml from the example."""

    def test_dry_run_logs_ingress_provision_when_absent(
        self, tmp_path: Path
    ) -> None:
        """Absent → dry-run logs the copy from deploy/ingress.toml.example."""
        _create_stub_seeds(tmp_path)
        result = _run_install_dry_run(tmp_path)
        assert result.returncode == 0, (
            f"install.sh --dry-run exited {result.returncode}.\n"
            f"stderr:\n{result.stderr}\nstdout:\n{result.stdout}"
        )
        assert "ingress.toml.example" in result.stdout, (
            "dry-run must log provisioning ingress.toml from the example.\n"
            f"stdout:\n{result.stdout}"
        )

    def test_dry_run_skips_ingress_when_present(self, tmp_path: Path) -> None:
        """Present → dry-run reports skip, never re-copies over operator edits."""
        _create_stub_seeds(tmp_path)
        ingress = tmp_path / ".roxabi" / "factory" / "ingress.toml"
        ingress.parent.mkdir(parents=True, exist_ok=True)
        ingress.write_text("[connector.github]\nenabled = true\n")
        result = _run_install_dry_run(tmp_path)
        assert result.returncode == 0, (
            f"install.sh --dry-run exited {result.returncode}.\n{result.stderr}"
        )
        assert "ingress.toml already exists" in result.stdout, (
            "dry-run must skip an existing ingress.toml.\n"
            f"stdout:\n{result.stdout}"
        )
