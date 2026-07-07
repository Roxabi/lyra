"""Tests for tools/emit_secrets_manifest.py — emitter + drift gate.

T14 contract map:
  A — parse_unit_secret_names: name=value.split(",")[0]; {{bot_secrets}} excluded
  B — manifest contains all 7 factory-nats-* (incl. factory-nats-gh-helper)
  C — policy classification correct (nats-seed / nats-auth / generated / optional)
  D — gate happy path: current tree → exit 0
  E — gate drift detection: unit Secret= with no required_secrets entry → exit non-zero
  F — owner=factory filter: acl-matrix owner/type partitioning; current tree passes
  G — optional secret absent → SKIP not failure
  I — --list-unit-secrets CLI mode: prints secret names for a given unit file
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
        "factory-nats-web",
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
            "factory-nats-web",
            "factory-nats-clipool",
            "factory-nats-turn-writer",
            "factory-nats-blobstore",
            "factory-nats-gh-helper",
            "factory-nats-ingress",
        ]
        for name in seed_names:
            assert name in policy.get("secret", {}), f"{name} missing from policy"
            assert policy["secret"][name]["policy"] == "nats-seed", (
                f"{name} should have policy=nats-seed"
            )

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

    def test_factory_nats_auth_absent_from_policy(self) -> None:
        """factory-nats-auth must NOT appear in secrets-policy.toml (ADR-085).

        auth.conf is delivered as a bind-mount, not a Podman secret.  Any entry
        for factory-nats-auth would contradict ADR-085 and risk creating a stale
        secret that silently shadows the bind-mount on the next converge.

        Non-tautology: if the entry is added to secrets-policy.toml, this test
        fails immediately — there is no code path that allows the key while this
        assertion is present.
        """
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)
        assert "factory-nats-auth" not in policy.get("secret", {}), (
            "factory-nats-auth must NOT be in secrets-policy.toml: "
            "auth.conf is a bind-mount (ADR-085), not a Podman secret"
        )


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
    """Bidirectional check (c) mutation tests — owner=factory filter is load-bearing.

    check (c) is now bidirectional:
      Forward:  every factory-nats-* in quadlet.toml required_secrets must have a
                matching acl-matrix identity (owner=factory, type=container).
      Reverse:  every acl-matrix identity (owner=factory, type=container) whose
                deploy.secret matches factory-nats-* must appear in quadlet.toml
                required_secrets.

    The owner==factory filter is load-bearing for the REVERSE direction: without
    it, external identities (e.g. voicecli-nats-tts with owner=voicecli) enter
    ACL_SECRETS and trigger a spurious reverse violation because their secrets
    are not in quadlet.toml.
    """

    def _copy_deploy_tree(self, tmp_path: Path) -> Path:
        """Copy deploy/ subtree to tmp so we can mutate it safely."""
        shutil.copytree(str(REPO_ROOT / "deploy"), str(tmp_path / "deploy"))
        return tmp_path

    def _gate_env(self, work: Path) -> dict[str, str]:
        """Build env overrides pointing the gate at the tmp deploy tree."""
        return {
            **os.environ,
            "QUADLET_TOML": str(work / "deploy" / "quadlet.toml"),
            "POLICY_TOML": str(work / "deploy" / "secrets-policy.toml"),
            "QUADLET_DIR": str(work / "deploy" / "quadlet"),
            "MANIFEST_SH": str(work / "deploy" / "generated" / "secrets-manifest.sh"),
            "ACL_MATRIX": str(work / "deploy" / "nats" / "acl-matrix.json"),
        }

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

    # ── data-partition tests (kept — verify ACL matrix is correctly partitioned)

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

    def test_factory_container_secrets_are_exactly_nine(self) -> None:
        """Exactly 11 factory container identities in the acl-matrix."""
        factory_container_secrets = self._factory_container_secrets()

        expected = {
            "factory-nats-hub",
            "factory-nats-telegram",
            "factory-nats-discord",
            "factory-nats-web",
            "factory-nats-clipool",
            "factory-nats-turn-writer",
            "factory-nats-blobstore",
            "factory-nats-gh-helper",
            "factory-nats-omp",
            "factory-nats-socialmedia",
            "factory-nats-ingress",
        }
        assert factory_container_secrets == expected, (
            "factory container secrets mismatch.\n"
            f"Expected: {sorted(expected)}\n"
            f"Got:      {sorted(factory_container_secrets)}"
        )

    # ── mutation test 1: reverse direction (quadlet.toml missing a nats secret)

    def test_reverse_direction_fails_when_quadlet_missing_nats_secret(
        self, tmp_path: Path
    ) -> None:
        """Removing factory-nats-* from quadlet required_secrets → FAIL (c) reverse.

        Mutation: delete factory-nats-gh-helper from the gh-helper component's
        required_secrets in quadlet.toml. The acl-matrix still has the identity,
        so the reverse check detects the dangling ACL entry.

        Regression signal: if the reverse direction is removed from
        check_secrets_drift.sh, this test passes on the mutated tree (no
        reverse violation raised) — the regression is invisible.
        """
        work = self._copy_deploy_tree(tmp_path)
        qtoml_path = work / "deploy" / "quadlet.toml"

        # Mutate: remove factory-nats-gh-helper from required_secrets.
        original = qtoml_path.read_text()
        # The entry is in a list — remove the quoted element and any trailing comma.
        mutated = original.replace('"factory-nats-gh-helper"', "")
        # Clean up any double-comma or comma-before-close-bracket artefacts.
        import re as _re

        mutated = _re.sub(r",\s*,", ",", mutated)
        mutated = _re.sub(r",\s*\]", "]", mutated)
        mutated = _re.sub(r"\[\s*,", "[", mutated)
        qtoml_path.write_text(mutated)

        # Mutated tree: gate must fail.
        result_fail = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=self._gate_env(work),
        )
        assert result_fail.returncode != 0, (
            "Gate must fail when factory-nats-gh-helper is absent from"
            " quadlet.toml required_secrets but present in acl-matrix.\n"
            f"stdout:\n{result_fail.stdout}\nstderr:\n{result_fail.stderr}"
        )
        combined_fail = result_fail.stdout + result_fail.stderr
        assert "(c) reverse" in combined_fail, (
            "Failure output must mention '(c) reverse' to distinguish from forward"
            " check.\n"
            f"stdout:\n{result_fail.stdout}\nstderr:\n{result_fail.stderr}"
        )
        assert "factory-nats-gh-helper" in combined_fail, (
            "Failure output must name the offending secret.\n"
            f"stdout:\n{result_fail.stdout}\nstderr:\n{result_fail.stderr}"
        )

        # Unmutated tree (fresh copy): gate must pass.
        work2 = self._copy_deploy_tree(tmp_path / "clean")
        result_ok = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=self._gate_env(work2),
        )
        assert result_ok.returncode == 0, (
            "Gate must pass on the unmutated tree.\n"
            f"stdout:\n{result_ok.stdout}\nstderr:\n{result_ok.stderr}"
        )

    # ── mutation test 2: filter is load-bearing (external identity promoted to factory)

    def test_filter_load_bearing_promoted_external_identity_triggers_reverse(
        self, tmp_path: Path
    ) -> None:
        """Promoting a voicecli identity to owner=factory triggers FAIL (c) reverse.

        Mutation: flip voice-tts from owner=voicecli → owner=factory (type=container
        already set). Its secret voicecli-nats-tts is not in quadlet.toml
        required_secrets, so the reverse check raises a violation.

        This test proves the owner==factory filter is load-bearing for the reverse
        direction: if the filter were removed (ACL_SECRETS became all-container
        identities regardless of owner), external identities with non-factory-nats-*
        secrets would NOT match the factory-nats-* pattern guard in the reverse loop
        — BUT if one had a factory-nats-* secret, it would be caught.  The more
        direct proof is that a mutated identity WITH owner=factory and a secret that
        IS factory-nats-prefixed (but absent from quadlet.toml) is caught, validating
        the round-trip through ACL_SECRETS.

        Regression signal: if the select(owner=="factory") filter is deleted AND the
        acl-matrix ever acquires an external identity with a factory-nats-* secret,
        the gate would miss a real violation.  This test fails if the reverse
        direction disappears from check_secrets_drift.sh.
        """
        work = self._copy_deploy_tree(tmp_path)
        acl_path = work / "deploy" / "nats" / "acl-matrix.json"

        # Mutate: promote voice-tts → owner=factory, keep type=container.
        # Its deploy.secret (voicecli-nats-tts) is not in quadlet.toml — reverse
        # check will complain IF the secret matches factory-nats-*.  To make this
        # a clean factory-nats-* reverse violation, we also change the secret name.
        with acl_path.open() as f:
            matrix = json.load(f)

        matrix["identities"]["voice-tts"]["owner"] = "factory"
        # Rename its deploy secret to a factory-nats-* name not in quadlet.toml.
        matrix["identities"]["voice-tts"]["deploy"]["secret"] = (
            "factory-nats-orphan-tts"
        )

        acl_path.write_text(json.dumps(matrix, indent=2))

        # Mutated tree: gate must fail on the reverse check.
        result_fail = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=self._gate_env(work),
        )
        assert result_fail.returncode != 0, (
            "Gate must fail when a factory+container acl-matrix identity has a"
            " factory-nats-* secret not in quadlet.toml required_secrets.\n"
            f"stdout:\n{result_fail.stdout}\nstderr:\n{result_fail.stderr}"
        )
        combined_fail = result_fail.stdout + result_fail.stderr
        assert "(c) reverse" in combined_fail, (
            "Reverse direction must be triggered by the promoted identity.\n"
            f"stdout:\n{result_fail.stdout}\nstderr:\n{result_fail.stderr}"
        )
        assert "factory-nats-orphan-tts" in combined_fail, (
            "Failure output must name the offending orphan secret.\n"
            f"stdout:\n{result_fail.stdout}\nstderr:\n{result_fail.stderr}"
        )

        # Unmutated tree: gate must pass.
        work2 = self._copy_deploy_tree(tmp_path / "clean")
        result_ok = subprocess.run(
            ["bash", str(GATE_SCRIPT)],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            env=self._gate_env(work2),
        )
        assert result_ok.returncode == 0, (
            "Gate must pass on the unmutated tree.\n"
            f"stdout:\n{result_ok.stdout}\nstderr:\n{result_ok.stderr}"
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
        """Policy file must declare exactly 8 optional secrets."""
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)
        optionals = [
            name
            for name, attrs in policy.get("secret", {}).items()
            if attrs.get("policy") == "optional"
        ]
        assert set(optionals) == {
            "factory-gh-pem",
            "factory-claude-oauth",
            "factory-litellm-key",
            "factory-socialmedia-api-key",
            "factory-ingress-github-webhook",
            "factory-ingress-cloudflare-webhook",
            "factory-telegram-monitor-token",
            "factory-telegram-monitor-chat-id",
        }, f"Unexpected optional secrets: {optionals}"


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


# ---------------------------------------------------------------------------
# Section I — --list-unit-secrets CLI mode
# ---------------------------------------------------------------------------


class TestListUnitSecretsCLI:
    """--list-unit-secrets <unit_file> prints one secret name per line."""

    def test_gh_helper_unit_lists_expected_secrets(self) -> None:
        """factory-gh-helper.container lists gh-pem and nats-gh-helper secrets."""
        unit = QUADLET_DIR / "factory-gh-helper.container"
        result = subprocess.run(
            [sys.executable, str(EMIT_SCRIPT), "--list-unit-secrets", str(unit)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0, (
            f"--list-unit-secrets failed.\nstderr: {result.stderr}"
        )
        names = result.stdout.strip().splitlines()
        assert "factory-gh-pem" in names
        assert "factory-nats-gh-helper" in names

    def test_hub_unit_lists_hub_seed(self) -> None:
        """factory-hub.container lists factory-nats-hub."""
        unit = QUADLET_DIR / "factory-hub.container"
        result = subprocess.run(
            [sys.executable, str(EMIT_SCRIPT), "--list-unit-secrets", str(unit)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        names = result.stdout.strip().splitlines()
        assert "factory-nats-hub" in names

    def test_bot_secrets_placeholder_excluded_from_cli_output(
        self, tmp_path: Path
    ) -> None:
        """{{bot_secrets}} placeholder must not appear in --list-unit-secrets output."""
        unit = tmp_path / "factory-telegram.container.tmpl"
        unit.write_text(
            "[Container]\n"
            "Secret={{bot_secrets}}\n"
            "Secret=factory-nats-telegram,type=mount\n"
        )
        result = subprocess.run(
            [sys.executable, str(EMIT_SCRIPT), "--list-unit-secrets", str(unit)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        names = result.stdout.strip().splitlines()
        assert "{{bot_secrets}}" not in names
        assert names == ["factory-nats-telegram"]

    def test_nonexistent_unit_exits_2(self, tmp_path: Path) -> None:
        """--list-unit-secrets with a nonexistent file exits 2."""
        result = subprocess.run(
            [
                sys.executable,
                str(EMIT_SCRIPT),
                "--list-unit-secrets",
                str(tmp_path / "does-not-exist.container"),
            ],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 2

    def test_empty_unit_produces_no_output(self, tmp_path: Path) -> None:
        """Unit with no Secret= lines produces empty stdout."""
        unit = tmp_path / "factory-nats.container"
        unit.write_text("[Container]\nImage=nats:latest\n")
        result = subprocess.run(
            [sys.executable, str(EMIT_SCRIPT), "--list-unit-secrets", str(unit)],
            capture_output=True,
            text=True,
        )
        assert result.returncode == 0
        assert result.stdout.strip() == ""


# ---------------------------------------------------------------------------
# Section J — optional secrets have concrete sources (not n/a)
# ---------------------------------------------------------------------------


class TestOptionalSecretConcreteSources:
    """factory-gh-pem and factory-claude-oauth have concrete source paths."""

    def test_optional_secrets_have_concrete_sources_in_policy(self) -> None:
        """Optional secrets must resolve to concrete file paths, not 'n/a'."""
        with POLICY_TOML.open("rb") as f:
            policy = tomllib.load(f)
        expected = {
            "factory-gh-pem": "gh-app.pem",
            "factory-claude-oauth": "claude-oauth.tok",
        }
        for name, expected_source in expected.items():
            entry = policy.get("secret", {}).get(name, {})
            assert entry.get("source") == expected_source, (
                f"{name} source should be {expected_source!r},"
                f" got {entry.get('source')!r}"
            )

    def test_optional_secrets_have_concrete_sources_in_manifest(self) -> None:
        """Regenerated manifest must list concrete source paths for optional secrets."""
        manifest_text = MANIFEST_SH.read_text()
        # factory-gh-pem → gh-app.pem
        assert '[factory-gh-pem]="gh-app.pem"' in manifest_text, (
            "factory-gh-pem must map to gh-app.pem in SECRET_SOURCES"
        )
        # factory-claude-oauth → claude-oauth.tok
        assert '[factory-claude-oauth]="claude-oauth.tok"' in manifest_text, (
            "factory-claude-oauth must map to claude-oauth.tok in SECRET_SOURCES"
        )
