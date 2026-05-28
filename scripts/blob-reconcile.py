#!/usr/bin/env python3
"""Orphan blob reconciler.

Walks the blob filesystem, compares against the `blobs` SQLite manifest,
and removes unreferenced content-addressed shards. Files younger than 1 hour
are skipped as a race guard (in-progress writes may exist on disk before
the DB commit).

Usage:
    python scripts/blob-reconcile.py [--dry-run] <blob_root> <sqlite_db>
"""

from __future__ import annotations

import argparse
import json
import sqlite3
import sys
import time
from pathlib import Path

# Skip files younger than this many seconds (race guard).
RACE_GUARD_SECONDS = 3600


def collect_db_paths(db_path: str) -> set[str]:
    """Return a set of absolute paths referenced in the blobs table."""
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    cursor = conn.cursor()
    cursor.execute("SELECT store_path FROM blobs")
    rows = {row["store_path"] for row in cursor.fetchall()}
    conn.close()
    return {str(Path(p).resolve()) for p in rows}


def collect_disk_files(blob_root: Path) -> list[Path]:
    """Return list of all sha256/* hex-named files under blob_root."""
    files: list[Path] = []
    sha256_dir = blob_root / "sha256"
    if sha256_dir.is_dir():
        for f in sha256_dir.iterdir():
            if f.is_file() and all(c in "0123456789abcdefABCDEF" for c in f.name):
                files.append(f)
    return files


def reconcile(blob_root: str, db_path: str, *, dry_run: bool) -> dict:
    """Run reconciliation and return a JSON report dict."""
    root = Path(blob_root).resolve()
    db_paths = collect_db_paths(db_path)
    disk_files = collect_disk_files(root)

    now = time.time()
    files_removed = 0
    bytes_reclaimed = 0

    for f in disk_files:
        abs_path = str(f)
        mtime = f.stat().st_mtime
        age = now - mtime

        if age < RACE_GUARD_SECONDS:
            continue

        if abs_path not in db_paths:
            size = f.stat().st_size
            if not dry_run:
                f.unlink()
            files_removed += 1
            bytes_reclaimed += size

    report = {
        "dry_run": dry_run,
        "files_removed": files_removed,
        "bytes_reclaimed": bytes_reclaimed,
        "blob_root": str(root),
        "db_path": str(Path(db_path).resolve()),
    }

    action = "Would remove" if dry_run else "Removed"
    msg = (
        f"{action} {files_removed} orphaned file(s),"
        f" reclaiming {bytes_reclaimed} byte(s)"
    )
    print(msg)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Orphan blob reconciler")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be removed without deleting",
    )
    parser.add_argument(
        "blob_root", help="Path to blob storage root (e.g. /data/lyra/blobs)"
    )
    parser.add_argument(
        "db_path", help="Path to SQLite manifest (e.g. ~/.lyra/blobstore/index.sqlite)"
    )
    args = parser.parse_args()

    report = reconcile(args.blob_root, args.db_path, dry_run=args.dry_run)
    print(json.dumps(report, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
