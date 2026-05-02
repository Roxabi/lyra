"""RED tests for scripts/check_request_reply_flows.py CLI — #1017 T06.

Tests invoke the CLI via subprocess. The module does not exist yet — that is the
intended RED state: every test will fail with a non-zero exit code or a FileNotFoundError.
"""

from __future__ import annotations

import copy
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CLI = REPO_ROOT / "scripts" / "check_request_reply_flows.py"
REAL_MATRIX = REPO_ROOT / "deploy" / "nats" / "acl-matrix.json"


def _run_cli(matrix_path: Path) -> subprocess.CompletedProcess[str]:
    """Run the check_request_reply_flows.py CLI against *matrix_path*."""
    return subprocess.run(
        [sys.executable, str(CLI), "--matrix", str(matrix_path)],
        capture_output=True,
        text=True,
        cwd=str(REPO_ROOT),
    )


def _write_matrix(tmp_path: Path, matrix: dict[str, Any]) -> Path:
    """Write *matrix* to a temp JSON file and return the path."""
    out = tmp_path / "matrix.json"
    out.write_text(json.dumps(matrix))
    return out


class TestPositiveCase:
    def test_prod_matrix_exits_zero(self) -> None:
        """check_request_reply_flows.py exits 0 for the production acl-matrix.json.

        SC-5 / SC-22: sibling validator must be a drop-in replacement for the bash script.
        # verified: breaking flow resolution in main() causes non-zero exit → fails
        """
        result = _run_cli(REAL_MATRIX)
        assert result.returncode == 0, (
            f"Expected exit 0 for prod matrix.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_v2_prod_fixture_exits_zero(self, prod_matrix: dict[str, Any], tmp_path: Path) -> None:
        """check_request_reply_flows.py exits 0 for v2-prod fixture.

        v2-prod has hub → clipool-worker flow; hub publishes lyra.clipool.cmd → covered.
        """
        path = _write_matrix(tmp_path, prod_matrix)
        result = _run_cli(path)
        assert result.returncode == 0, (
            f"Expected exit 0 for v2-prod fixture.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )


class TestMissingRequesterIdentity:
    def test_missing_requester_exits_nonzero(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """CLI exits non-zero when a flow references a requester not in identities.

        # verified: removing the requester-existence check causes exit 0 → assertion fails
        """
        matrix = copy.deepcopy(prod_matrix)
        matrix["request_reply_flows"].append({
            "requester": "nonexistent-requester",
            "responder": "clipool-worker",
            "subject": "lyra.test.cmd",
        })
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        assert result.returncode != 0
        assert "nonexistent-requester" in result.stdout or "nonexistent-requester" in result.stderr

    def test_missing_requester_error_message(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """CLI output names the missing requester identity."""
        matrix = copy.deepcopy(prod_matrix)
        matrix["request_reply_flows"].append({
            "requester": "ghost-requester",
            "responder": "hub",
            "subject": "lyra.ghost.cmd",
        })
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        combined = result.stdout + result.stderr
        assert "ghost-requester" in combined


class TestMissingResponderIdentity:
    def test_missing_responder_exits_nonzero(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """CLI exits non-zero when a flow references a responder not in identities.

        # verified: removing the responder-existence check causes exit 0 → assertion fails
        """
        matrix = copy.deepcopy(prod_matrix)
        matrix["request_reply_flows"].append({
            "requester": "hub",
            "responder": "nonexistent-worker",
            "subject": "lyra.clipool.cmd",
        })
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        assert result.returncode != 0
        combined = result.stdout + result.stderr
        assert "nonexistent-worker" in combined

    def test_missing_responder_error_message(
        self, prod_matrix: dict[str, Any], tmp_path: Path
    ) -> None:
        """CLI output names the missing responder identity."""
        matrix = copy.deepcopy(prod_matrix)
        matrix["request_reply_flows"].append({
            "requester": "hub",
            "responder": "phantom-responder",
            "subject": "lyra.clipool.cmd",
        })
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        combined = result.stdout + result.stderr
        assert "phantom-responder" in combined


class TestSubjectNotCoveredByPublish:
    def test_subject_not_covered_exits_nonzero(self, tmp_path: Path) -> None:
        """CLI exits non-zero when requester's publish[] does not cover the flow subject.

        hub publishes lyra.foo.> but the flow subject is lyra.bar.cmd — no match.
        # verified: removing subject-coverage check causes exit 0 → assertion fails
        """
        matrix = {
            "version": "2",
            "request_reply_flows": [
                {"requester": "hub", "responder": "clipool-worker", "subject": "lyra.bar.cmd"}
            ],
            "identities": {
                "hub": {
                    "status": "active",
                    "created_at": "2026-04-21",
                    "owner": "lyra",
                    "description": "hub",
                    "allow_responses": False,
                    "publish": ["lyra.foo.>"],
                    "subscribe": [],
                },
                "clipool-worker": {
                    "status": "active",
                    "created_at": "2026-04-27",
                    "owner": "lyra",
                    "description": "clipool worker",
                    "allow_responses": True,
                    "publish": [],
                    "subscribe": ["lyra.bar.cmd"],
                },
            },
        }
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        assert result.returncode != 0

    def test_exact_subject_mismatch_exits_nonzero(self, tmp_path: Path) -> None:
        """CLI exits non-zero when requester publish is lyra.foo but subject is lyra.foo.bar."""
        matrix = {
            "version": "2",
            "request_reply_flows": [
                {"requester": "hub", "responder": "worker", "subject": "lyra.foo.bar"}
            ],
            "identities": {
                "hub": {
                    "status": "active",
                    "created_at": "2026-04-21",
                    "owner": "lyra",
                    "description": "hub",
                    "allow_responses": False,
                    "publish": ["lyra.foo"],
                    "subscribe": [],
                },
                "worker": {
                    "status": "active",
                    "created_at": "2026-04-27",
                    "owner": "lyra",
                    "description": "worker",
                    "allow_responses": True,
                    "publish": [],
                    "subscribe": ["lyra.foo.bar"],
                },
            },
        }
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        assert result.returncode != 0


class TestSubjectCoveredByWildcard:
    def test_wildcard_suffix_covers_subject(self, tmp_path: Path) -> None:
        """CLI exits 0 when requester publishes lyra.clipool.> and flow subject is lyra.clipool.cmd.

        NATS wildcard: lyra.clipool.> matches lyra.clipool.cmd (and any sub-level).
        # verified: removing wildcard matching causes lyra.clipool.cmd to fail coverage check
        """
        matrix = {
            "version": "2",
            "request_reply_flows": [
                {"requester": "hub", "responder": "clipool-worker", "subject": "lyra.clipool.cmd"}
            ],
            "identities": {
                "hub": {
                    "status": "active",
                    "created_at": "2026-04-21",
                    "owner": "lyra",
                    "description": "hub",
                    "allow_responses": False,
                    "publish": ["lyra.clipool.>"],
                    "subscribe": [],
                },
                "clipool-worker": {
                    "status": "active",
                    "created_at": "2026-04-27",
                    "owner": "lyra",
                    "description": "clipool worker",
                    "allow_responses": True,
                    "publish": [],
                    "subscribe": ["lyra.clipool.cmd"],
                },
            },
        }
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        assert result.returncode == 0, (
            f"Expected exit 0 with wildcard coverage.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_exact_match_covers_subject(self, tmp_path: Path) -> None:
        """CLI exits 0 when requester's publish list contains the exact flow subject."""
        matrix = {
            "version": "2",
            "request_reply_flows": [
                {"requester": "hub", "responder": "worker", "subject": "lyra.exact.cmd"}
            ],
            "identities": {
                "hub": {
                    "status": "active",
                    "created_at": "2026-04-21",
                    "owner": "lyra",
                    "description": "hub",
                    "allow_responses": False,
                    "publish": ["lyra.exact.cmd"],
                    "subscribe": [],
                },
                "worker": {
                    "status": "active",
                    "created_at": "2026-04-27",
                    "owner": "lyra",
                    "description": "worker",
                    "allow_responses": True,
                    "publish": [],
                    "subscribe": ["lyra.exact.cmd"],
                },
            },
        }
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        assert result.returncode == 0, (
            f"Expected exit 0 with exact match.\nstdout: {result.stdout}\nstderr: {result.stderr}"
        )

    def test_no_flows_exits_zero(self, tmp_path: Path) -> None:
        """CLI exits 0 when request_reply_flows is empty."""
        matrix = {
            "version": "2",
            "request_reply_flows": [],
            "identities": {
                "hub": {
                    "status": "active",
                    "created_at": "2026-04-21",
                    "owner": "lyra",
                    "description": "hub",
                    "allow_responses": False,
                    "publish": [],
                    "subscribe": [],
                },
            },
        }
        path = _write_matrix(tmp_path, matrix)
        result = _run_cli(path)

        assert result.returncode == 0
