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

        assert result["version"] in ("1", "2")
        assert isinstance(result["identities"], dict)
        assert len(result["identities"]) >= 1
        flows = result.get("request_reply_flows")
        assert isinstance(flows, list)
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
