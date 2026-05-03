"""RED tests for scripts/check_acl_matrix_retired.py — #1017 Wave 2 T05.

The CLI (scripts/check_acl_matrix_retired.py) does not exist yet.
All tests MUST FAIL (returncode != 0 due to missing script).

Covers all 5 error classes from scripts/check-acl-matrix-retired.sh:
  EC-1  Missing status field on any identity
  EC-2  Missing created_at on any identity
  EC-3  Bad date format on created_at
  EC-4  Retired identity without retired_at
  EC-5  Retired identity still referenced in request_reply_flows
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "check_acl_matrix_retired.py"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _valid_active_identity() -> dict[str, Any]:
    return {
        "status": "active",
        "created_at": "2026-04-21",
        "owner": "lyra",
        "description": "Test identity",
        "allow_responses": False,
        "publish": ["lyra.test.>"],
        "subscribe": ["lyra.test.cmd"],
    }


def _valid_retired_identity() -> dict[str, Any]:
    return {
        "status": "retired",
        "created_at": "2025-01-01",
        "retired_at": "2025-06-01",
        "owner": "reserved",
        "description": "Legacy retired identity",
        "allow_responses": False,
        "publish": ["lyra.old.heartbeat"],
        "subscribe": ["lyra.old.cmd"],
    }


def _write_matrix(tmp_path: Path, data: dict[str, Any]) -> Path:
    p = tmp_path / "matrix.json"
    p.write_text(json.dumps(data))
    return p


def _run_cli(matrix_path: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(CLI), "--matrix", str(matrix_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


# ---------------------------------------------------------------------------
# Positive — prod matrix exits 0
# ---------------------------------------------------------------------------


class TestCheckAclMatrixRetiredPositive:
    def test_prod_matrix_exits_0(self, prod_matrix_path: Path) -> None:
        """Positive: prod acl-matrix.json has no lifecycle errors; CLI must exit 0."""
        result = _run_cli(prod_matrix_path)
        assert result.returncode == 0, (
            f"CLI exited {result.returncode}\n"
            f"stdout: {result.stdout}\nstderr: {result.stderr}"
        )


# ---------------------------------------------------------------------------
# EC-1 — Missing status field
# ---------------------------------------------------------------------------


class TestMissingStatus:
    def test_valid_identity_passes(self, tmp_path: Path) -> None:
        """EC-1 passing variant: identity with status field exits 0."""
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": _valid_active_identity()},
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode == 0

    def test_missing_status_exits_1(self, tmp_path: Path) -> None:
        """EC-1 failing variant: identity missing status field must exit 1.

        Guard: check_acl_matrix_retired must reject identities that lack the
        'status' field (mirrors check-acl-matrix-retired.sh line 15).
        """
        identity = _valid_active_identity()
        del identity["status"]
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# EC-2 — Missing created_at
# ---------------------------------------------------------------------------


class TestMissingCreatedAt:
    def test_valid_identity_passes(self, tmp_path: Path) -> None:
        """EC-2 passing variant: identity with created_at field exits 0."""
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": _valid_active_identity()},
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode == 0

    def test_missing_created_at_exits_1(self, tmp_path: Path) -> None:
        """EC-2 failing variant: identity missing created_at must exit 1.

        Guard: check_acl_matrix_retired must reject identities that lack the
        'created_at' field (mirrors check-acl-matrix-retired.sh line 16).
        """
        identity = _valid_active_identity()
        del identity["created_at"]
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# EC-3 — Bad date format on created_at
# ---------------------------------------------------------------------------


class TestBadCreatedAtFormat:
    def test_valid_date_format_passes(self, tmp_path: Path) -> None:
        """EC-3 passing variant: created_at in YYYY-MM-DD format exits 0."""
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": _valid_active_identity()},
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode == 0

    def test_bad_created_at_format_exits_1(self, tmp_path: Path) -> None:
        """EC-3 failing variant: created_at with invalid date format must exit 1.

        Guard: check_acl_matrix_retired must reject dates that don't match
        YYYY-MM-DD (mirrors check-acl-matrix-retired.sh line 17).
        """
        identity = _valid_active_identity()
        identity["created_at"] = "21/04/2026"  # wrong format
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {"hub": identity},
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# EC-4 — Retired identity without retired_at
# ---------------------------------------------------------------------------


class TestRetiredWithoutRetiredAt:
    def test_retired_with_retired_at_passes(self, tmp_path: Path) -> None:
        """EC-4 passing variant: retired identity with retired_at exits 0."""
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {
                "hub": _valid_active_identity(),
                "old-worker": _valid_retired_identity(),
            },
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode == 0

    def test_retired_missing_retired_at_exits_1(self, tmp_path: Path) -> None:
        """EC-4 failing variant: retired identity without retired_at must exit 1.

        Guard: check_acl_matrix_retired must require 'retired_at' on any
        identity whose status is 'retired' (mirrors check-acl-matrix-retired.sh
        line 21).
        """
        retired = _valid_retired_identity()
        del retired["retired_at"]
        data = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {
                "hub": _valid_active_identity(),
                "old-worker": retired,
            },
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode != 0


# ---------------------------------------------------------------------------
# EC-5 — Retired identity still referenced in request_reply_flows
# ---------------------------------------------------------------------------


class TestRetiredInFlows:
    def test_retired_not_in_flows_passes(self, tmp_path: Path) -> None:
        """EC-5 passing variant: retired identity not referenced in flows exits 0."""
        data = {
            "version": "2",
            "request_reply_flows": [
                {
                    "requester": "hub",
                    "responder": "voice-tts",
                    "subject": "lyra.voice.tts.request.>",
                },
            ],
            "identities": {
                "hub": _valid_active_identity(),
                "voice-tts": _valid_active_identity(),
                "old-worker": _valid_retired_identity(),
            },
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode == 0

    def test_retired_in_flows_as_requester_exits_1(self, tmp_path: Path) -> None:
        """EC-5 failing variant: retired identity as requester in flows must exit 1.

        Guard: check_acl_matrix_retired must reject matrices where a retired
        identity still appears in request_reply_flows (mirrors
        check-acl-matrix-retired.sh lines 23-25).
        """
        data = {
            "version": "2",
            "request_reply_flows": [
                {
                    "requester": "old-worker",
                    "responder": "hub",
                    "subject": "lyra.old.cmd",
                },
            ],
            "identities": {
                "hub": _valid_active_identity(),
                "old-worker": _valid_retired_identity(),
            },
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode != 0

    def test_retired_in_flows_as_responder_exits_1(self, tmp_path: Path) -> None:
        """EC-5 failing variant: retired identity as responder in flows must exit 1.

        Guard: check_acl_matrix_retired must reject matrices where a retired
        identity still appears as a responder in request_reply_flows (mirrors
        check-acl-matrix-retired.sh lines 23-25).
        """
        data = {
            "version": "2",
            "request_reply_flows": [
                {
                    "requester": "hub",
                    "responder": "old-worker",
                    "subject": "lyra.old.cmd",
                },
            ],
            "identities": {
                "hub": _valid_active_identity(),
                "old-worker": _valid_retired_identity(),
            },
        }
        result = _run_cli(_write_matrix(tmp_path, data))
        assert result.returncode != 0
