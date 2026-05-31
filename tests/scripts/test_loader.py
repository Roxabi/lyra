"""RED tests for scripts/_loader.py — #1017 Wave 2 T02.

These tests MUST FAIL until scripts/_loader.py is implemented.
Import is deferred to test-function scope so pytest collection succeeds
even when the module is absent (ImportError surfaces per-test, not at
collection time).
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

# REPO and fixture paths re-used from conftest
from tests.scripts.conftest import FIXTURES_DIR

_V2_PROD = FIXTURES_DIR / "v2-prod.json"
_V1_LEGACY = FIXTURES_DIR / "v1-legacy.json"

# ---------------------------------------------------------------------------
# Helper — build a minimal valid identity dict
# ---------------------------------------------------------------------------


def _valid_identity(
    *,
    status: str = "active",
    owner: str = "lyra",
    created_at: str = "2026-04-21",
    retired_at: str | None = None,
) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "status": status,
        "owner": owner,
        "created_at": created_at,
        "description": "Test identity",
        "allow_responses": False,
        "publish": ["lyra.test.>"],
        "subscribe": ["lyra.test.cmd"],
    }
    if retired_at is not None:
        entry["retired_at"] = retired_at
    return entry


def _write_matrix(tmp_path: Path, data: dict[str, Any]) -> Path:
    """Serialise *data* to tmp_path/matrix.json and return the path."""
    p = tmp_path / "matrix.json"
    p.write_text(json.dumps(data))
    return p


# ---------------------------------------------------------------------------
# Positive
# ---------------------------------------------------------------------------


class TestLoadMatrixPositive:
    def test_load_matrix_prod_v2(self, prod_matrix_path: Path) -> None:
        """load_matrix returns a LoadedMatrix with all five top-level keys populated."""
        from scripts._loader import load_matrix  # noqa: PLC0415

        result = load_matrix(prod_matrix_path)

        assert result["version"] in ("1", "2", "3", "4")
        assert isinstance(result["identities"], dict)
        assert len(result["identities"]) >= 1
        flows = result.get("request_reply_flows")
        assert isinstance(flows, list)
        # Prod matrix has flows at every version — guard the invariant unconditionally.
        assert len(flows) >= 1
        # Spot-check one identity key set
        first_identity = next(iter(result["identities"].values()))
        for key in (
            "owner",
            "status",
            "description",
            "publish",
            "subscribe",
            "allow_responses",
            "created_at",
        ):
            assert key in first_identity, f"identity missing key: {key}"


# ---------------------------------------------------------------------------
# Negatives — each must raise SystemExit(1)
# ---------------------------------------------------------------------------


class TestLoadMatrixNegatives:
    def test_missing_owner_field(self, tmp_path: Path) -> None:
        """Guard: identity must have owner field."""
        # verified: removing owner-field check in _loader.py causes this test to fail
        identity = _valid_identity()
        del identity["owner"]
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_missing_status_field(self, tmp_path: Path) -> None:
        """Guard: identity must have status field."""
        # verified: removing status-field check in _loader.py causes this test to fail
        identity = _valid_identity()
        del identity["status"]
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_invalid_status_value(self, tmp_path: Path) -> None:
        """Guard: identity status must be 'active' or 'retired'."""
        # verified: removing status allowlist guard → test fails
        identity = _valid_identity(status="unknown")
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_invalid_owner_value(self, tmp_path: Path) -> None:
        """Guard: identity owner must be one of lyra|voicecli|imagecli|reserved."""
        # verified: removing owner allowlist guard → test fails
        identity = _valid_identity(owner="badowner")
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_invalid_version(self, tmp_path: Path) -> None:
        """Guard: version must be '1' or '2'."""
        # verified: removing version allowlist guard → test fails
        identity = _valid_identity()
        data = {
            "version": "99",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_missing_created_at(self, tmp_path: Path) -> None:
        """Guard: identity must have created_at field."""
        # verified: removing created_at field guard → test fails
        identity = _valid_identity()
        del identity["created_at"]
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_bad_created_at_format(self, tmp_path: Path) -> None:
        """Guard: created_at must be a valid ISO date (YYYY-MM-DD)."""
        # verified: removing date-format validation guard → test fails
        identity = _valid_identity(created_at="not-a-date")
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_bad_retired_at_format(self, tmp_path: Path) -> None:
        """Guard: retired_at must be a valid ISO date (YYYY-MM-DD) when present."""
        # verified: removing retired_at date-format guard → test fails
        identity = _valid_identity(
            status="retired",
            created_at="2025-01-01",
            retired_at="not-a-date",
        )
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"test-id": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_duplicate_flow_pair(self, tmp_path: Path) -> None:
        """Guard: request_reply_flows must not have duplicate (requester, responder)."""
        # verified: removing duplicate-flow check in _loader.py causes this test to fail
        data = {
            "version": "2",
            "request_reply_flows": [
                {
                    "requester": "hub",
                    "responder": "clipool-worker",
                    "subject": "lyra.clipool.cmd",
                },
                {
                    "requester": "hub",
                    "responder": "clipool-worker",
                    "subject": "lyra.clipool.other",
                },
            ],
            "identities": {
                "hub": _valid_identity(owner="lyra"),
                "clipool-worker": _valid_identity(owner="lyra"),
            },
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0

    def test_nonexistent_file(self, tmp_path: Path) -> None:
        """Guard: load_matrix raises SystemExit when the path does not exist."""
        # verified: removing file-existence check in _loader.py causes this test to fail
        missing = tmp_path / "does-not-exist.json"

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(missing)
        assert exc_info.value.code != 0


# ---------------------------------------------------------------------------
# Parity — v1 and v2 fixtures must expose identical identity keys
# ---------------------------------------------------------------------------


class TestLoadMatrixParity:
    def test_v1_and_v2_load_same_identities(self) -> None:
        """v1-legacy.json and v2-prod.json share the same identity names."""
        from scripts._loader import load_matrix  # noqa: PLC0415

        v1 = load_matrix(_V1_LEGACY)
        v2 = load_matrix(_V2_PROD)

        assert set(v1["identities"].keys()) == set(v2["identities"].keys())


# ---------------------------------------------------------------------------
# Deploy field — RED tests for #1379 (T1).
# T2 adds Deploy TypedDict variants to _acl_models.py.
# T3 adds _validate_deploy() to _loader.py.
# T12 extends _VALID_VERSIONS to include "3".
# T13 adds v3 enforcement that 'deploy' is required on active identities.
# All 5 tests below MUST FAIL until those tasks land.
# ---------------------------------------------------------------------------


class TestDeployField:
    def test_load_matrix_v2_without_deploy_ok(self, tmp_path: Path) -> None:
        """Regression guard: v2 matrix with no deploy field on an active identity
        must load cleanly — deploy is optional in v2.

        RED until T2 lands: imports ContainerDeploy/ExternalDeploy/ManagedDeploy
        from _acl_models (added in T2). Once T2 lands these imports resolve and
        load_matrix must not reject a deploy-less v2 identity.

        verified: if _validate_identity rejects identities lacking 'deploy' at
        v2, this test fails.
        """
        # Arrange — T2 adds Deploy TypedDicts; import fails until then
        from scripts._acl_models import ContainerDeploy  # noqa: PLC0415  # T2

        identity = _valid_identity(status="active")
        assert "deploy" not in identity  # explicit: NO deploy field
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        # Act
        from scripts._loader import load_matrix  # noqa: PLC0415

        result = load_matrix(path)

        # Assert
        assert result["version"] == "2"
        assert "hub" in result["identities"]
        # identity has no deploy key — ContainerDeploy imported for type reference only
        assert "deploy" not in result["identities"]["hub"]
        _ = ContainerDeploy  # silence unused-import lint; type is used as documentation

    def test_load_matrix_v2_with_deploy_ok(self, tmp_path: Path) -> None:
        """Forward-compat: v2 matrix with a valid deploy field must load cleanly.

        RED until T2 lands: imports ContainerDeploy from _acl_models and
        asserts the loaded identity's deploy matches the typed structure.

        verified: if load_matrix rejects recognised 'deploy' keys at v2,
        this test fails.
        """
        # Arrange — T2 adds ContainerDeploy; import fails until then
        from scripts._acl_models import ContainerDeploy  # noqa: PLC0415  # T2

        identity = _valid_identity(status="active")
        identity["deploy"] = {"type": "container", "secret": "x"}  # type: ignore[index]
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        # Act
        from scripts._loader import load_matrix  # noqa: PLC0415

        result = load_matrix(path)

        # Assert
        assert result["version"] == "2"
        assert "hub" in result["identities"]
        loaded_deploy = result["identities"]["hub"]["deploy"]  # type: ignore[typeddict-item]
        assert loaded_deploy["type"] == "container"
        # Verify it round-trips as a ContainerDeploy-compatible dict
        _typed: ContainerDeploy = loaded_deploy  # type: ignore[assignment]
        assert _typed["secret"] == "x"

    def test_load_matrix_v3_active_missing_deploy_dies(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """v3 matrix with an active identity missing deploy must die with
        a message matching "v3 requires 'deploy'".

        verified: removing the v3 active-deploy guard from _validate_identity
        causes this test to fail (different stderr message).
        """
        # Arrange
        identity = _valid_identity(status="active")
        assert "deploy" not in identity
        data = {
            "version": "3",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        # Act / Assert
        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert "v3 requires 'deploy'" in captured.err

    def test_load_matrix_external_missing_target_path_dies(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """external deploy without target_path must die with a clear error.

        verified: removing the target_path guard from _validate_deploy causes
        this test to fail.
        """
        # Arrange
        identity = _valid_identity(status="active")
        identity["deploy"] = {"type": "external", "host": "foo"}  # type: ignore[index]
        # target_path deliberately absent
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        # Act / Assert
        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert "external missing 'host' or 'target_path'" in captured.err

    def test_load_matrix_container_missing_secret_dies(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """container deploy without secret must die with a clear error.

        verified: removing the secret guard from _validate_deploy causes
        this test to fail.
        """
        # Arrange
        identity = _valid_identity(status="active")
        identity["deploy"] = {"type": "container"}  # type: ignore[index]
        # secret deliberately absent
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        # Act / Assert
        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert "container missing 'secret'" in captured.err

    def test_load_matrix_v2_host_deploy_ok(self, tmp_path: Path) -> None:
        """v2 matrix with host deploy variant loads cleanly.

        verified: covers the host branch of _validate_deploy that the other
        TestDeployField cases (container/external) leave untested.
        """
        identity = _valid_identity(status="active")
        identity["deploy"] = {  # type: ignore[index]
            "type": "host",
            "path": "/run/lyra/nkeys/hub.seed",
        }
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        result = load_matrix(path)
        loaded = result["identities"]["hub"]["deploy"]  # type: ignore[typeddict-item]
        assert loaded["type"] == "host"
        assert loaded["path"] == "/run/lyra/nkeys/hub.seed"

    def test_load_matrix_host_missing_path_dies(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """host deploy without path must die with a clear error.

        verified: removing the path guard from _validate_deploy causes
        this test to fail.
        """
        identity = _valid_identity(status="active")
        identity["deploy"] = {"type": "host"}  # type: ignore[index]
        # path deliberately absent
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert "host missing 'path'" in captured.err


# ---------------------------------------------------------------------------
# v4 groups — T10 (S4 ACL grant-group, #1526)
# ---------------------------------------------------------------------------


def _valid_identity_with_deploy(
    *,
    status: str = "active",
    owner: str = "lyra",
    groups: list[str] | None = None,
) -> dict:
    """Return a minimal valid active identity with deploy field (required in v3/v4)."""
    entry: dict = {
        "status": status,
        "owner": owner,
        "created_at": "2026-05-01",
        "description": "Test identity",
        "allow_responses": False,
        "publish": ["lyra.test.>"],
        "subscribe": ["lyra.test.cmd"],
        "deploy": {"type": "container", "secret": "lyra-nats-test"},
    }
    if groups is not None:
        entry["groups"] = groups
    return entry


class TestLoadMatrixV4Groups:
    """T10 (a, b) — v4 matrix with valid groups loads correctly."""

    def test_v4_with_groups_loads_ok(self, tmp_path: Path) -> None:
        """v4 matrix with a valid top-level 'groups' and identity referencing it loads
        without error and load_matrix(...)['groups'] contains the defined group.

        verified: removing _parse_groups call from load_matrix would either drop
        the 'groups' key from the result or raise KeyError, failing this assertion.
        """
        # Arrange
        data = {
            "version": "4",
            "request_reply_flows": [],
            "groups": {
                "audio-consumers": {
                    "publish": ["$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO"],
                    "subscribe": ["lyra.outbound.audio.>"],
                }
            },
            "identities": {
                "telegram-adapter": _valid_identity_with_deploy(
                    groups=["audio-consumers"]
                ),
            },
        }
        path = _write_matrix(tmp_path, data)

        # Act
        from scripts._loader import load_matrix  # noqa: PLC0415

        result = load_matrix(path)

        # Assert — group key present and identity loads
        assert result["version"] == "4"
        assert "telegram-adapter" in result["identities"]
        assert "audio-consumers" in result["groups"]  # type: ignore[typeddict-item]

    def test_v4_groups_parsed_into_loaded_matrix(self, tmp_path: Path) -> None:
        """load_matrix['groups'] exposes each group with 'publish'/'subscribe' lists.

        verified: stripping publish/subscribe from GroupDefinition or omitting groups
        from LoadedMatrix would fail the shape assertion.
        """
        # Arrange
        data = {
            "version": "4",
            "request_reply_flows": [],
            "groups": {
                "audio-consumers": {
                    "description": "Adapter audio grant bundle",
                    "publish": ["$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO"],
                    "subscribe": ["lyra.outbound.audio.>"],
                },
                "kv-readers": {
                    "publish": [],
                    "subscribe": ["$KV.lyra-state.>"],
                },
            },
            "identities": {
                "hub": _valid_identity_with_deploy(
                    groups=["audio-consumers", "kv-readers"]
                ),
            },
        }
        path = _write_matrix(tmp_path, data)

        # Act
        from scripts._loader import load_matrix  # noqa: PLC0415

        result = load_matrix(path)
        groups = result["groups"]  # type: ignore[typeddict-item]

        # Assert — shape of each group
        assert isinstance(groups, dict)
        assert set(groups.keys()) == {"audio-consumers", "kv-readers"}

        ag = groups["audio-consumers"]
        assert isinstance(ag["publish"], list)
        assert isinstance(ag["subscribe"], list)
        assert "$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO" in ag["publish"]
        assert "lyra.outbound.audio.>" in ag["subscribe"]

        kv = groups["kv-readers"]
        assert isinstance(kv["publish"], list)
        assert kv["publish"] == []
        assert "$KV.lyra-state.>" in kv["subscribe"]


class TestLoadMatrixV4GroupsNegatives:
    """T10 (c, d) — v4 guard: undefined group ref and missing deploy."""

    def test_v4_identity_referencing_undefined_group_dies(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """Identity referencing a group name not present in top-level 'groups' must die.

        verified: removing _validate_group_refs from load_matrix causes this test to
        pass silently — the undefined ref goes undetected.
        """
        # Arrange
        data = {
            "version": "4",
            "request_reply_flows": [],
            "groups": {
                "audio-consumers": {
                    "publish": ["$JS.API.STREAM.INFO.LYRA_OUTBOUND_AUDIO"],
                    "subscribe": [],
                }
            },
            "identities": {
                "telegram-adapter": _valid_identity_with_deploy(
                    groups=["audio-consumers", "does-not-exist"]
                ),
            },
        }
        path = _write_matrix(tmp_path, data)

        # Act / Assert
        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert "does-not-exist" in captured.err

    def test_v4_active_identity_missing_deploy_dies(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        """v4 matrix with an active identity missing deploy must die.

        The deploy guard inherited from v3 must apply equally to v4.

        verified: removing version '4' from the guard condition in _validate_identity
        (i.e. keeping only v3) causes this test to pass silently — the v4 missing-
        deploy identity would load without error.
        """
        # Arrange — active identity without deploy (plain _valid_identity omits it)
        identity = _valid_identity(status="active")
        assert "deploy" not in identity
        data = {
            "version": "4",
            "request_reply_flows": [],
            "groups": {},
            "identities": {"hub": identity},
        }
        path = _write_matrix(tmp_path, data)

        # Act / Assert
        from scripts._loader import load_matrix  # noqa: PLC0415

        with pytest.raises(SystemExit) as exc_info:
            load_matrix(path)
        assert exc_info.value.code != 0
        captured = capsys.readouterr()
        assert "v3 requires 'deploy'" in captured.err
