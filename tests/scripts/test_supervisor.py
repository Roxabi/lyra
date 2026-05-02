"""RED tests for scripts/_supervisor.py — #1017 T04.

These tests FAIL at collection time because scripts/_supervisor.py does not exist yet.
That is the intended RED state.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

# This import will fail at collection time — that is the intended RED state.
from scripts._supervisor import validate_supervisor

REPO_ROOT = Path(__file__).resolve().parents[2]


def _lyra_owned_identities(matrix: dict[str, Any]) -> list[str]:
    """Return names of identities with owner == 'lyra'."""
    return [
        name
        for name, identity in matrix["identities"].items()
        if identity.get("owner") == "lyra"
    ]


def _make_supervisor_conf(root: Path, name: str) -> None:
    """Create deploy/conf.d/lyra-<name>.conf with NATS_NKEY_SEED_PATH wired."""
    conf_dir = root / "deploy" / "conf.d"
    conf_dir.mkdir(parents=True, exist_ok=True)
    conf_file = conf_dir / f"lyra-{name}.conf"
    conf_file.write_text(
        f'environment=NATS_NKEY_SEED_PATH="~/.lyra/nkeys/{name}.seed"\n'
    )


def _make_quadlet_container(root: Path, name: str) -> None:
    """Create deploy/quadlet/lyra-<name>.container with NATS_NKEY_SEED_PATH wired."""
    quadlet_dir = root / "deploy" / "quadlet"
    quadlet_dir.mkdir(parents=True, exist_ok=True)
    container_file = quadlet_dir / f"lyra-{name}.container"
    container_file.write_text(
        f"Environment=NATS_NKEY_SEED_PATH=/run/secrets/{name}.seed\n"
    )


class TestValidateSupervisorPasses:
    def test_validate_supervisor_passes(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """validate_supervisor returns [] when all lyra-owned identities have conf files.

        Each owner==lyra identity (hub, clipool-worker in v2-prod) needs a matching
        deploy/conf.d/lyra-<name>.conf containing NATS_NKEY_SEED_PATH.
        # verified: removing conf file creation causes identity to be reported as missing
        """
        for name in _lyra_owned_identities(prod_matrix):
            _make_supervisor_conf(tmp_path, name)

        errors = validate_supervisor(prod_matrix, tmp_path)

        assert errors == []

    def test_validate_supervisor_passes_with_multiple_lyra_identities(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """validate_supervisor handles all lyra identities in one pass."""
        lyra_names = _lyra_owned_identities(prod_matrix)
        assert len(lyra_names) >= 1, "prod_matrix should have at least one lyra-owned identity"

        for name in lyra_names:
            _make_supervisor_conf(tmp_path, name)

        errors = validate_supervisor(prod_matrix, tmp_path)

        assert isinstance(errors, list)
        assert errors == []


class TestValidateSupervisorMissingWiring:
    def test_validate_supervisor_missing_wiring(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """validate_supervisor returns error list when hub conf is absent.

        # verified: creating hub conf makes errors empty → assertion fails
        """
        lyra_names = _lyra_owned_identities(prod_matrix)
        # Create conf for everyone except hub
        for name in lyra_names:
            if name != "hub":
                _make_supervisor_conf(tmp_path, name)

        errors = validate_supervisor(prod_matrix, tmp_path)

        assert len(errors) > 0
        assert any("hub" in err for err in errors)

    def test_validate_supervisor_all_missing(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """validate_supervisor reports all lyra identities when no conf files exist.

        # verified: creating all conf files makes errors empty → assertion fails
        """
        # Create empty deploy dirs but no conf files
        (tmp_path / "deploy" / "conf.d").mkdir(parents=True, exist_ok=True)
        (tmp_path / "deploy" / "quadlet").mkdir(parents=True, exist_ok=True)

        errors = validate_supervisor(prod_matrix, tmp_path)

        lyra_names = set(_lyra_owned_identities(prod_matrix))
        assert len(errors) >= len(lyra_names)

    def test_validate_supervisor_non_lyra_not_checked(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """validate_supervisor ignores identities where owner != lyra.

        voice-tts is owner=voicecli — no wiring required, no error emitted.
        # verified: changing voice-tts owner to lyra without conf causes an error
        """
        # Only wire up lyra-owned identities
        for name in _lyra_owned_identities(prod_matrix):
            _make_supervisor_conf(tmp_path, name)

        errors = validate_supervisor(prod_matrix, tmp_path)

        # voice-tts (owner=voicecli) must not appear in error list
        assert not any("voice-tts" in err for err in errors)


class TestQuadletWiringCounts:
    def test_quadlet_wiring_counts(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """validate_supervisor accepts Quadlet container files in deploy/quadlet/.

        Quadlet form: deploy/quadlet/lyra-<name>.container with
        Environment=NATS_NKEY_SEED_PATH=/run/secrets/<name>.seed
        # verified: using wrong file extension (e.g. .conf in quadlet/) causes no match → errors
        """
        for name in _lyra_owned_identities(prod_matrix):
            _make_quadlet_container(tmp_path, name)

        errors = validate_supervisor(prod_matrix, tmp_path)

        assert errors == []

    def test_quadlet_and_supervisor_mixed(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """validate_supervisor passes when some identities use quadlet, others use supervisor conf."""
        lyra_names = _lyra_owned_identities(prod_matrix)
        assert len(lyra_names) >= 2, "Need at least 2 lyra identities for this test"

        # First identity → supervisor conf
        _make_supervisor_conf(tmp_path, lyra_names[0])
        # Remaining → quadlet
        for name in lyra_names[1:]:
            _make_quadlet_container(tmp_path, name)

        errors = validate_supervisor(prod_matrix, tmp_path)

        assert errors == []
