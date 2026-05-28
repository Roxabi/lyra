"""Tests for scripts/blob-reconcile.py — orphan blob reconciler."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "blob-reconcile.py"


class TestBlobReconcile:
    """End-to-end CLI tests for the orphan blob reconciler."""

    def _setup_blob_dir(self, tmp_path: Path) -> Path:
        blob_root = tmp_path / "blobs"
        sha256_dir = blob_root / "sha256"
        sha256_dir.mkdir(parents=True)
        return blob_root

    def _make_hex_file(
        self,
        sha256_dir: Path,
        name: str,
        content: bytes,
        mtime: float | None = None,
    ) -> Path:
        f = sha256_dir / name
        f.write_bytes(content)
        if mtime is not None:
            os.utime(f, (mtime, mtime))
        return f

    def _setup_db(
        self,
        db_path: Path,
        entries: list[tuple[str, str, int]],
    ) -> None:
        conn = sqlite3.connect(str(db_path))
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS blobs (
                content_hash   TEXT PRIMARY KEY,
                mime           TEXT NOT NULL,
                size           INTEGER NOT NULL,
                store_path     TEXT NOT NULL,
                first_seen_at  TEXT NOT NULL
            )
            """
        )
        for content_hash, store_path, size in entries:
            conn.execute(
                "INSERT INTO blobs VALUES (?, ?, ?, ?, ?)",
                (
                    content_hash,
                    "application/octet-stream",
                    size,
                    store_path,
                    "2024-01-01T00:00:00Z",
                ),
            )
        conn.commit()
        conn.close()

    def _run(self, blob_root: Path, db_path: Path, dry_run: bool = False) -> dict:
        cmd = [sys.executable, str(SCRIPT)]
        if dry_run:
            cmd.append("--dry-run")
        cmd.extend([str(blob_root), str(db_path)])
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )
        assert result.returncode == 0, (
            f"Non-zero exit: {result.returncode}\n"
            f"stderr: {result.stderr}\n"
            f"stdout: {result.stdout}"
        )
        # Parse JSON from stdout — find first line starting with {
        # and join all subsequent lines (JSON is pretty-printed multi-line)
        lines = result.stdout.splitlines()
        json_start = next(
            (i for i, line in enumerate(lines) if line.startswith("{")),
            None,
        )
        assert json_start is not None, "No JSON line found in stdout"
        json_text = "\n".join(lines[json_start:])
        return json.loads(json_text)

    def _run_expect_error(
        self, blob_root: Path, db_path: Path, dry_run: bool = False
    ) -> subprocess.CompletedProcess[str]:
        cmd = [sys.executable, str(SCRIPT)]
        if dry_run:
            cmd.append("--dry-run")
        cmd.extend([str(blob_root), str(db_path)])
        return subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
        )

    # ── Core behaviour ─────────────────────────────────────────────────────────

    def test_referenced_files_not_deleted(self, tmp_path: Path) -> None:
        """Files present in the blobs table must survive reconciliation."""
        # Arrange
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "blobs.db"
        f = self._make_hex_file(
            blob_root / "sha256",
            "abc123def4567890abcdef1234567890abcdef12",
            b"ref",
        )
        self._setup_db(
            db_path,
            [("abc123def4567890abcdef1234567890abcdef12", str(f), 3)],
        )

        # Act
        report = self._run(blob_root, db_path)

        # Assert
        assert f.exists()
        assert report["files_removed"] == 0
        assert report["bytes_reclaimed"] == 0

    def test_orphans_removed(self, tmp_path: Path) -> None:
        """Unreferenced files older than the race guard are deleted."""
        # Arrange
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "blobs.db"
        ref = self._make_hex_file(
            blob_root / "sha256",
            "abc123def4567890abcdef1234567890abcdef12",
            b"ref",
        )
        orphan = self._make_hex_file(
            blob_root / "sha256",
            "def4567890abcdef1234567890abcdef1234567890",
            b"orphan",
            mtime=time.time() - 7200,
        )
        self._setup_db(
            db_path,
            [("abc123def4567890abcdef1234567890abcdef12", str(ref), 3)],
        )

        # Act
        report = self._run(blob_root, db_path)

        # Assert
        assert ref.exists()
        assert not orphan.exists()
        assert report["files_removed"] == 1
        assert report["bytes_reclaimed"] == 6

    def test_dry_run_does_not_delete(self, tmp_path: Path) -> None:
        """--dry-run reports orphans but leaves them on disk."""
        # Arrange
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "blobs.db"
        orphan = self._make_hex_file(
            blob_root / "sha256",
            "def4567890abcdef1234567890abcdef1234567890",
            b"orphan",
            mtime=time.time() - 7200,
        )
        self._setup_db(db_path, [])

        # Act
        report = self._run(blob_root, db_path, dry_run=True)

        # Assert
        assert orphan.exists()
        assert report["files_removed"] == 1
        assert report["bytes_reclaimed"] == 6
        assert report["dry_run"] is True

    def test_race_guard_skips_young_files(self, tmp_path: Path) -> None:
        """Files with mtime < 3600s are skipped even if not in the DB."""
        # Arrange
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "blobs.db"
        young = self._make_hex_file(
            blob_root / "sha256",
            "def4567890abcdef1234567890abcdef1234567890",
            b"young",
            mtime=time.time(),
        )
        self._setup_db(db_path, [])

        # Act
        report = self._run(blob_root, db_path)

        # Assert
        assert young.exists()
        assert report["files_removed"] == 0
        assert report["bytes_reclaimed"] == 0

    def test_non_hex_files_ignored(self, tmp_path: Path) -> None:
        """Files with non-hex names in sha256/ are ignored by the walker."""
        # Arrange
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "blobs.db"
        non_hex = blob_root / "sha256" / "not_a_hex_file.txt"
        non_hex.write_bytes(b"ignore me")
        os.utime(non_hex, (time.time() - 7200, time.time() - 7200))
        self._setup_db(db_path, [])

        # Act
        report = self._run(blob_root, db_path)

        # Assert
        assert non_hex.exists()
        assert report["files_removed"] == 0

    def test_report_structure(self, tmp_path: Path) -> None:
        """The JSON report contains all required fields with correct types."""
        # Arrange
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "blobs.db"
        self._setup_db(db_path, [])

        # Act
        report = self._run(blob_root, db_path)

        # Assert
        assert set(report.keys()) == {
            "dry_run",
            "files_removed",
            "bytes_reclaimed",
            "blob_root",
            "db_path",
            "unlink_errors",
        }
        assert isinstance(report["dry_run"], bool)
        assert isinstance(report["files_removed"], int)
        assert isinstance(report["bytes_reclaimed"], int)
        assert isinstance(report["blob_root"], str)
        assert isinstance(report["db_path"], str)
        assert isinstance(report["unlink_errors"], list)
        assert Path(report["blob_root"]).exists()
        assert Path(report["db_path"]).exists()
        assert report["files_removed"] == 0
        assert report["bytes_reclaimed"] == 0
        assert report["unlink_errors"] == []

    # ── Error-path coverage ────────────────────────────────────────────────────

    def test_missing_db_table(self, tmp_path: Path) -> None:
        """DB without a blobs table → error report."""
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "blobs.db"
        conn = sqlite3.connect(str(db_path))
        conn.execute("CREATE TABLE other (id INTEGER PRIMARY KEY)")
        conn.commit()
        conn.close()

        result = self._run_expect_error(blob_root, db_path)
        assert result.returncode == 1
        report = json.loads(result.stderr)
        assert "no such table" in report["error"]

    def test_nonexistent_blob_root(self, tmp_path: Path) -> None:
        """Non-existent blob_root → error report with resolved path."""
        db_path = tmp_path / "blobs.db"
        self._setup_db(db_path, [])
        blob_root = tmp_path / "nonexistent_blobs"

        result = self._run_expect_error(blob_root, db_path)
        assert result.returncode == 1
        report = json.loads(result.stderr)
        assert Path(report["blob_root"]).exists() is False

    def test_missing_db_file(self, tmp_path: Path) -> None:
        """Missing DB file → non-zero exit."""
        blob_root = self._setup_blob_dir(tmp_path)
        db_path = tmp_path / "missing.db"

        result = self._run_expect_error(blob_root, db_path)
        assert result.returncode != 0
        assert "database file not found" in result.stderr
