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
    with sqlite3.connect(db_path) as conn:
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        try:
            cursor.execute("SELECT store_path FROM blobs")
            rows = {row["store_path"] for row in cursor.fetchall()}
        except sqlite3.OperationalError:
            rows = set()
    return {str(Path(p).resolve()) for p in rows}


def collect_disk_files(blob_root: Path) -> list[Path]:
    """Return list of all hex-named files under blob_root (recursive)."""
    files: list[Path] = []
    if not blob_root.exists():
        return files
    for f in blob_root.rglob("*"):
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
    unlink_errors: list[str] = []

    for f in disk_files:
        abs_path = str(f)
        mtime = f.stat().st_mtime
        age = now - mtime

        if age < RACE_GUARD_SECONDS:
            continue

        if abs_path not in db_paths:
            size = f.stat().st_size
            if not dry_run:
                try:
                    f.unlink()
                except OSError as exc:
                    unlink_errors.append(f"{abs_path}: {exc}")
                    continue
            files_removed += 1
            bytes_reclaimed += size

    report = {
        "dry_run": dry_run,
        "files_removed": files_removed,
        "bytes_reclaimed": bytes_reclaimed,
        "blob_root": str(root),
        "db_path": str(Path(db_path).resolve()),
        "unlink_errors": unlink_errors,
    }

    action = "Would remove" if dry_run else "Removed"
    msg = (
        f"{action} {files_removed} orphaned file(s),"
        f" reclaiming {bytes_reclaimed} byte(s)"
    )
    if unlink_errors:
        msg += f"; {len(unlink_errors)} unlink error(s)"
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

    if not Path(args.db_path).exists():
        error_report = {
            "error": f"database file not found: {args.db_path}",
            "blob_root": str(Path(args.blob_root).resolve()),
            "db_path": str(Path(args.db_path).resolve()),
        }
        print(json.dumps(error_report, indent=2), file=sys.stderr)
        return 1

    try:
        report = reconcile(args.blob_root, args.db_path, dry_run=args.dry_run)
        print(json.dumps(report, indent=2))
        return 0
    except (OSError, sqlite3.Error) as exc:
        error_report = {
            "error": str(exc),
            "blob_root": str(Path(args.blob_root).resolve()),
            "db_path": str(Path(args.db_path).resolve()),
        }
        print(json.dumps(error_report, indent=2), file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
