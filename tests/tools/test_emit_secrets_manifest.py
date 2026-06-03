"""Tests for tools/emit_secrets_manifest.py — emitter + drift gate.

T14 contract map:
  A — parse_unit_secret_names: name=value.split(",")[0]; {{bot_secrets}} excluded
  B — manifest contains all 7 factory-nats-* (incl. factory-nats-gh-helper)
  C — policy classification correct (nats-seed / nats-auth / generated / optional)
  D — gate happy path: current tree → exit 0
  E — gate drift detection: unit Secret= with no required_secrets entry → exit non-zero
  F — owner=factory filter: voicecli-nats-* → no false-positive failure
  G — optional secret absent → SKIP not failure
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest
from tools.emit_secrets_manifest import parse_unit_secret_names

# ── locate repo root so we can import and shell-out correctly ─────────────────

REPO_ROOT = Path(__file__).resolve().parents[2]
EMIT_SCRIPT = REPO_ROOT / "tools" / "emit_secrets_manifest.py"
GATE_SCRIPT = REPO_ROOT / "tools" / "check_secrets_drift.sh"
QUADLET_TOML = REPO_ROOT / "deploy" / "quadlet.toml"
POLICY_TOML = REPO_ROOT / "deploy" / "secrets-policy.toml"
MANIFEST_SH = REPO_ROOT / "deploy" / "generated" / "secrets-manifest.sh"
ACL_MATRIX = REPO_ROOT / "deploy" / "nats" / "acl-matrix.json"
QUADLET_DIR = REPO_ROOT / "deploy" / "quadlet"


# ---------------------------------------------------------------------------
# Section A — parse_unit_secret_names
# ---------------------------------------------------------------------------


class TestParseUnitSecretNames:
    """Unit tests for the Secret= parse contract."""

    def test_simple_name_no_options(self, tmp_path: Path) -> None:
        """Bare Secret=name returns [name]."""
        unit = tmp_path / "factory-hub.container"
        unit.write_text("[Container]\nSecret=factory-nats-hub\n")

        result = parse_unit_secret_names(unit)

        assert result == ["factory-nats-hub"]

    def test_name_split_on_first_comma(self, tmp_path: Path) -> None:
        """name=value.split(',')[0] strips Podman options."""
        unit = tmp_path / "factory-nats.container"
        unit.write_text(
            "[Container]\n"
            "Secret=factory-nats-auth"
            ",type=mount,target=/etc/nats/nkeys/auth.conf,mode=0444\n"
        )

        result = parse_unit_secret_names(unit)

        assert result == ["factory-nats-auth"]

    def test_bot_secrets_placeholder_excluded(self, tmp_path: Path) -> None:
        """{{bot_secrets}} template placeholder must never appear in output."""
        unit = tmp_path / "factory-telegram.container.tmpl"
        unit.write_text(
            "[Container]\n"
            "Secret={{bot_secrets}}\n"
            "Secret=factory-nats-telegram,type=mount\n"
        )

        result = parse_unit_secret_names(unit)

        assert "{{bot_secrets}}" not in result
        assert result == ["factory-nats-telegram"]

    def test_bot_secrets_mixed_with_options_excluded(self, tmp_path: Path) -> None:
        """{{bot_secrets}} with comma-appended options is still excluded."""
        unit = tmp_path / "factory-telegram.container.tmpl"
        unit.write_text("[Container]\nSecret={{bot_secrets}},type=mount\n")

        result = parse_unit_secret_names(unit)

        assert result == []

    def test_multiple_secrets_all_parsed(self, tmp_path: Path) -> None:
        """Multiple Secret= lines all captured."""
        unit = tmp_path / "factory-gh-helper.container"
        unit.write_text(
            "[Container]\n"
            "Secret=factory-gh-pem"
            ",type=mount,target=gh-app.pem,mode=0400,uid=1501,gid=1501\n"
            "Secret=factory-nats-gh-helper"
            ",type=mount,target=factory-nats-gh-helper.seed"
            ",uid=1501,gid=1501,mode=0400\n"
        )

        result = parse_unit_secret_names(unit)

        assert "factory-gh-pem" in result
        assert "factory-nats-gh-helper" in result
        assert len(result) == 2

    def test_non_secret_lines_ignored(self, tmp_path: Path) -> None:
        """Lines that don't start with Secret= are not returned."""
        unit = tmp_path / "factory-hub.container"
        unit.write_text(
            "[Container]\n"
            "Image=ghcr.io/roxabi/factory:staging\n"
            "# Secret=commented-out-secret\n"
            "Secret=factory-nats-hub\n"
        )

        result = parse_unit_secret_names(unit)

        assert result == ["factory-nats-hub"]

    def test_empty_unit_returns_empty_list(self, tmp_path: Path) -> None:
        """No Secret= lines → empty list."""
        unit = tmp_path / "factory-nats.container"
        unit.write_text("[Container]\nImage=nats:latest\n")

        result = parse_unit_secret_names(unit)

        assert result == []


# ---------------------------------------------------------------------------
# Section B — manifest completeness (all 7 factory-nats-* present)
# ---------------------------------------------------------------------------


class TestManifestCompleteness:
    """Verify the committed manifest covers all 7 factory-nats-* secrets."""

    EXPECTED_NATS_SEEDS = {
        "factory-nats-hub",
        "factory-nats-telegram",
        "factory-nats-discord",
        "factory-nats-clipool",
        "factory-nats-turn-writer",
        "factory-nats-blobstore",
        "factory-nats-gh-helper",  # the #9 miss this PR fixes
    }

    def _parse_manifest_keys(self, manifest: Path) -> set[str]:
        """Extract key names from the SECRET_SOURCES declare -A block."""
        keys: set[str] = set()
        for line in manifest.read_text().splitlines():
            stripped = line.strip()
            if stripped.startswith("[") and "]=" in stripped:
                name = stripped[1 : stripped.index("]=")]
                keys.add(name)
        return keys

    def test_all_seven_nats_seeds_present(self) -> None:
        """Manifest must contain all 7 factory-nats-* seeds (incl. gh-helper)."""
        manifest_keys = self._parse_manifest_keys(MANIFEST_SH)

        missing = self.EXPECTED_NATS_SEEDS - manifest_keys
        assert not missing, f"factory-nats-* seeds missing from manifest: {missing}"

    def test_factory_nats_gh_helper_present(self) -> None:
        """factory-nats-gh-helper is the specific addition fixed in #1718."""
        manifest_keys = self._parse_manifest_keys(MANIFEST_SH)
        assert "factory-nats-gh-helper" in manifest_keys

    def test_quadlet_toml_gh_helper_required_secrets(self) -> None:
        """gh-helper component in quadlet.toml declares factory-nats-gh-helper."""
        with QUADLET_TOML.open("rb") as f:
            quadlet = tomllib.load(f)

        components = quadlet.get("component", {})
        gh_helper = components.get("gh-helper", {})
        required = gh_helper.get("required_secrets", [])

        assert "factory-nats-gh-helper" in required, (
            "quadlet.toml gh-helper.required_secrets must include"
            " factory-nats-gh-helper"
        )


# ---------------------------------------------------------------------------
# Section C — policy classification
# ---------------------------------------------------------------------------


class TestPolicyClassification:
    """Verify secrets-policy.toml classifications are correct."""

    def test_nats_seeds_have_nats_seed_policy(self) -> None:
        """All factory-nats-<role> seeds (excl. auth) carry policy=nats-seed."""
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)

        seed_names = [
            "factory-nats-hub",
            "factory-nats-telegram",
            "factory-nats-discord",
            "factory-nats-clipool",
            "factory-nats-turn-writer",
            "factory-nats-blobstore",
            "factory-nats-gh-helper",
        ]
        for name in seed_names:
            assert name in policy.get("secret", {}), f"{name} missing from policy"
            assert policy["secret"][name]["policy"] == "nats-seed", (
                f"{name} should have policy=nats-seed"
            )

    def test_nats_auth_has_nats_auth_policy(self) -> None:
        """factory-nats-auth carries policy=nats-auth (not nats-seed)."""
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)
        assert policy["secret"]["factory-nats-auth"]["policy"] == "nats-auth"

    def test_optional_secrets_have_optional_policy(self) -> None:
        """factory-gh-pem and factory-claude-oauth carry policy=optional."""
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)
        for name in ("factory-gh-pem", "factory-claude-oauth"):
            assert policy["secret"][name]["policy"] == "optional", (
                f"{name} should have policy=optional"
            )

    def test_blobstore_token_has_generated_policy(self) -> None:
        """factory_blobstore_token carries policy=generated."""
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)
        assert policy["secret"]["factory_blobstore_token"]["policy"] == "generated"


# ---------------------------------------------------------------------------
# Section D — gate happy path (current tree)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not available")
class TestGateHappyPath:
    """check_secrets_drift.sh against the current (correct) tree → exit 0."""

    def test_gate_passes_on_current_tree(self) -> None:
        """The drift gate must exit 0 against the committed tree."""
        result = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "check_secrets_drift.sh failed on the current tree.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Section E — gate drift detection
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not available")
class TestGateDriftDetection:
    """Inject undeclared Secret= → gate must exit non-zero."""

    def _copy_tree_to_tmp(self, tmp_path: Path) -> Path:
        """Copy the deploy/ subtree to tmp so we can mutate it safely."""
        deploy_src = REPO_ROOT / "deploy"
        deploy_dst = tmp_path / "deploy"
        shutil.copytree(str(deploy_src), str(deploy_dst))
        return tmp_path

    def test_undeclared_unit_secret_fails_check_a(self, tmp_path: Path) -> None:
        """Adding a Secret= not in required_secrets triggers FAIL (a)."""
        work = self._copy_tree_to_tmp(tmp_path)

        # Inject a phantom secret into the hub unit.
        hub_unit = work / "deploy" / "quadlet" / "factory-hub.container"
        original = hub_unit.read_text()
        hub_unit.write_text(original + "Secret=undeclared-phantom-secret\n")

        env = {
            **os.environ,
            "QUADLET_TOML": str(work / "deploy" / "quadlet.toml"),
            "POLICY_TOML": str(work / "deploy" / "secrets-policy.toml"),
            "QUADLET_DIR": str(work / "deploy" / "quadlet"),
            "MANIFEST_SH": str(work / "deploy" / "generated" / "secrets-manifest.sh"),
            "ACL_MATRIX": str(work / "deploy" / "nats" / "acl-matrix.json"),
        }

        result = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode != 0, (
            "Gate should have failed when a unit declares an undeclared secret.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        assert (
            "undeclared-phantom-secret" in result.stderr or "FAIL (a)" in result.stderr
        )

    def test_gate_detects_manifest_out_of_sync(self, tmp_path: Path) -> None:
        """Adding required_secrets entry without regenerating manifest fails (b)."""
        work = self._copy_tree_to_tmp(tmp_path)

        # Add a new component with a secret not in the manifest.
        qtoml_path = work / "deploy" / "quadlet.toml"
        original = qtoml_path.read_text()
        qtoml_path.write_text(
            original
            + "\n[component.test-probe]\n"
            + 'container = "factory-test-probe.container"\n'
            + 'required_secrets = ["factory-probe-orphan"]\n'
            + 'host_roles = ["factory-hub"]\n'
        )

        env = {
            **os.environ,
            "QUADLET_TOML": str(qtoml_path),
            "POLICY_TOML": str(work / "deploy" / "secrets-policy.toml"),
            "QUADLET_DIR": str(work / "deploy" / "quadlet"),
            "MANIFEST_SH": str(work / "deploy" / "generated" / "secrets-manifest.sh"),
            "ACL_MATRIX": str(work / "deploy" / "nats" / "acl-matrix.json"),
        }

        result = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=env,
        )

        assert result.returncode != 0, (
            "Gate should detect quadlet.toml / manifest mismatch.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )


# ---------------------------------------------------------------------------
# Section F — owner=factory filter (THE key correctness test)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not available")
class TestOwnerFactoryFilter:
    """voicecli-nats-* / external identities must NOT cause false-positive failures.

    This is the core correctness check: check (c) filters by
    owner=='factory' AND deploy.type=='container'.  External identities
    (voicecli-nats-tts, voicecli-nats-stt, llm-worker, image-worker …)
    must be silently ignored even though they share the same NATS cluster.
    """

    def _factory_container_secrets(self) -> set[str]:
        """Identities where owner=factory AND type=container."""
        with ACL_MATRIX.open() as f:
            matrix = json.load(f)
        return {
            identity["deploy"]["secret"]
            for identity in matrix["identities"].values()
            if (
                identity.get("owner") == "factory"
                and identity.get("deploy", {}).get("type") == "container"
            )
        }

    def test_voicecli_identities_not_factory_container(self) -> None:
        """voicecli-nats-tts and voicecli-nats-stt have owner=voicecli, not factory."""
        with ACL_MATRIX.open() as f:
            matrix = json.load(f)

        for name, data in matrix["identities"].items():
            if data.get("owner") == "voicecli":
                deploy_secret = data.get("deploy", {}).get("secret", "")
                # voicecli identities must not appear in factory container set
                assert deploy_secret not in self._factory_container_secrets(), (
                    f"Identity {name!r} (owner=voicecli) must not appear as"
                    " a factory container identity"
                )

    def test_factory_container_secrets_are_exactly_seven(self) -> None:
        """Exactly 7 factory container identities in the acl-matrix."""
        factory_container_secrets = self._factory_container_secrets()

        expected = {
            "factory-nats-hub",
            "factory-nats-telegram",
            "factory-nats-discord",
            "factory-nats-clipool",
            "factory-nats-turn-writer",
            "factory-nats-blobstore",
            "factory-nats-gh-helper",
        }
        assert factory_container_secrets == expected, (
            "factory container secrets mismatch.\n"
            f"Expected: {sorted(expected)}\n"
            f"Got:      {sorted(factory_container_secrets)}"
        )

    def test_gate_does_not_fail_with_external_identities_present(self) -> None:
        """Gate exits 0 even though acl-matrix has voicecli-nats-* identities.

        NEGATIVE TEST: if the owner=factory+container filter is deleted from
        check_secrets_drift.sh, voicecli-nats-tts/stt would appear as
        required seeds that have no matching quadlet.toml entry, causing a
        false-positive failure. With the filter, the gate must still pass.
        """
        result = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            "Gate must pass the current tree regardless of external"
            " acl-matrix identities.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_acl_check_c_output_shows_ok(self) -> None:
        """Gate stdout must contain the check (c) OK line when tree is clean."""
        result = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert "check_secrets_drift (c):" in result.stdout
        assert "OK" in result.stdout


# ---------------------------------------------------------------------------
# Section G — optional secret absent → SKIP not failure
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not shutil.which("jq"), reason="jq not available")
class TestOptionalSecretSkip:
    """factory-gh-pem and factory-claude-oauth absent → gate logs SKIP, exits 0."""

    def test_optional_secrets_do_not_fail_gate(self) -> None:
        """Gate exit 0 regardless of optional secret presence."""
        result = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"Gate failed unexpectedly.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )
        # If optional secrets are mentioned, they must be SKIP, not FAIL.
        if "factory-gh-pem" in result.stdout:
            assert "SKIP" in result.stdout
        if "factory-claude-oauth" in result.stdout:
            assert "SKIP" in result.stdout

    def test_policy_toml_optional_count(self) -> None:
        """Policy file must declare exactly 2 optional secrets."""
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)
        optionals = [
            name
            for name, attrs in policy.get("secret", {}).items()
            if attrs.get("policy") == "optional"
        ]
        assert set(optionals) == {"factory-gh-pem", "factory-claude-oauth"}, (
            f"Unexpected optional secrets: {optionals}"
        )


# ---------------------------------------------------------------------------
# Section H — emitter integration (run emit_secrets_manifest.py)
# ---------------------------------------------------------------------------


class TestEmitterIntegration:
    """Run emit_secrets_manifest.py to verify output on the current tree."""

    def test_emitter_exits_zero_on_current_tree(self) -> None:
        """emit_secrets_manifest.py exits 0 on the current committed tree."""
        result = subprocess.run(
            [sys.executable, str(EMIT_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"emit_secrets_manifest.py failed.\n"
            f"stdout:\n{result.stdout}\nstderr:\n{result.stderr}"
        )

    def test_emitter_output_contains_gh_helper(self) -> None:
        """Regenerated manifest must contain factory-nats-gh-helper."""
        result = subprocess.run(
            [sys.executable, str(EMIT_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert "factory-nats-gh-helper" in MANIFEST_SH.read_text()

    def test_emitter_fails_on_undeclared_policy(self, tmp_path: Path) -> None:
        """Emitter exits 1 when required_secrets references a name not in policy."""
        work = tmp_path
        deploy_dst = work / "deploy"
        shutil.copytree(str(REPO_ROOT / "deploy"), str(deploy_dst))

        # Add a new required_secret with no matching policy entry.
        qtoml = deploy_dst / "quadlet.toml"
        original = qtoml.read_text()
        qtoml.write_text(
            original
            + "\n[component.orphan]\n"
            + 'container = "factory-orphan.container"\n'
            + 'required_secrets = ["factory-orphan-undeclared"]\n'
            + 'host_roles = ["factory-hub"]\n'
        )

        # Copy the emitter script to run against the tmp tree.
        tools_tmp = work / "tools"
        tools_tmp.mkdir(exist_ok=True)
        (tools_tmp / "emit_secrets_manifest.py").write_text(EMIT_SCRIPT.read_text())

        result = subprocess.run(
            [sys.executable, "tools/emit_secrets_manifest.py"],
            cwd=str(work),
            capture_output=True,
            text=True,
        )
        assert result.returncode == 1, (
            "Emitter should exit 1 when required_secrets has no policy entry.\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )
        assert "factory-orphan-undeclared" in result.stderr
