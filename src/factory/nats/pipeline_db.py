"""SQLite persistence for PipelineStore (plane ④ read model)."""

from __future__ import annotations

import logging
import os
import sqlite3
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from factory.nats.pipeline_models import PipelineCheckRow, PipelineRunRow

log = logging.getLogger(__name__)

_MAX_PROCESSED_ROWS = 10_000

_DDL = """
CREATE TABLE IF NOT EXISTS pipeline_runs (
    repo              TEXT NOT NULL,
    pr_number         INTEGER NOT NULL,
    title             TEXT NOT NULL,
    head_sha          TEXT,
    head_ref          TEXT,
    html_url          TEXT,
    reviewed          INTEGER NOT NULL DEFAULT 0,
    open              INTEGER NOT NULL DEFAULT 1,
    ci_status         TEXT NOT NULL,
    merge_status      TEXT NOT NULL,
    publish_status    TEXT NOT NULL,
    m1_deploy_status  TEXT NOT NULL,
    cf_deploy_status  TEXT NOT NULL,
    publish_sha       TEXT,
    last_event_at     TEXT,
    updated_at        TEXT,
    PRIMARY KEY (repo, pr_number)
);

CREATE TABLE IF NOT EXISTS pipeline_checks (
    repo        TEXT NOT NULL,
    pr_number   INTEGER NOT NULL,
    name        TEXT NOT NULL,
    status      TEXT NOT NULL,
    conclusion  TEXT,
    PRIMARY KEY (repo, pr_number, name)
);

CREATE TABLE IF NOT EXISTS pipeline_processed (
    trace_id      TEXT PRIMARY KEY,
    processed_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS pipeline_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
);
"""


def default_db_path() -> Path:
    raw = os.environ.get("PIPELINE_DB_PATH", "").strip()
    if raw:
        return Path(raw)
    return Path.home() / ".roxabi" / "factory" / "pipeline.db"


class PipelineDb:
    """Sync sqlite3 backend for pipeline projection rows."""

    def __init__(self, db_path: str | Path) -> None:
        self._db_path = str(db_path)
        self._conn: sqlite3.Connection | None = None

    def connect(self) -> None:
        if self._conn is not None:
            return
        path = Path(self._db_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        try:
            self._conn.execute("PRAGMA journal_mode=WAL")
        except sqlite3.OperationalError:
            log.warning(
                "pipeline_db: WAL unavailable for %s — using default journal",
                self._db_path,
            )
        self._conn.execute("PRAGMA busy_timeout=30000")
        self._conn.executescript(_DDL)
        self._conn.commit()
        log.info("PipelineDb connected (db=%s)", self._db_path)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def has_runs(self) -> bool:
        conn = self._require_conn()
        row = conn.execute("SELECT 1 FROM pipeline_runs LIMIT 1").fetchone()
        return row is not None

    def load_runs(self) -> list[PipelineRunRow]:
        from factory.nats.pipeline_models import PipelineCheckRow, PipelineRunRow

        conn = self._require_conn()
        runs: list[PipelineRunRow] = []
        for row in conn.execute("SELECT * FROM pipeline_runs ORDER BY pr_number"):
            checks = [
                PipelineCheckRow(
                    name=str(c["name"]),
                    status=str(c["status"]),
                    conclusion=c["conclusion"],
                )
                for c in conn.execute(
                    "SELECT name, status, conclusion FROM pipeline_checks "
                    "WHERE repo = ? AND pr_number = ?",
                    (row["repo"], row["pr_number"]),
                )
            ]
            runs.append(
                PipelineRunRow(
                    repo=str(row["repo"]),
                    pr_number=int(row["pr_number"]),
                    title=str(row["title"]),
                    head_sha=row["head_sha"],
                    head_ref=row["head_ref"],
                    html_url=row["html_url"],
                    reviewed=bool(row["reviewed"]),
                    open=bool(row["open"]),
                    ci_status=row["ci_status"],
                    merge_status=row["merge_status"],
                    publish_status=row["publish_status"],
                    m1_deploy_status=row["m1_deploy_status"],
                    cf_deploy_status=row["cf_deploy_status"],
                    publish_sha=row["publish_sha"],
                    checks=checks,
                    last_event_at=row["last_event_at"],
                    updated_at=row["updated_at"],
                )
            )
        return runs

    def load_processed(self) -> set[str]:
        conn = self._require_conn()
        rows = conn.execute(
            "SELECT trace_id FROM pipeline_processed "
            "ORDER BY processed_at DESC LIMIT ?",
            (_MAX_PROCESSED_ROWS,),
        ).fetchall()
        return {str(r["trace_id"]) for r in rows}

    def load_last_publish_sha(self) -> str | None:
        conn = self._require_conn()
        row = conn.execute(
            "SELECT value FROM pipeline_meta WHERE key = 'last_publish_sha'"
        ).fetchone()
        if row is None:
            return None
        value = row["value"]
        return str(value) if value else None

    def upsert_run(self, row: PipelineRunRow) -> None:
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO pipeline_runs ("
            "repo, pr_number, title, head_sha, head_ref, html_url, reviewed, open, "
            "ci_status, merge_status, publish_status, m1_deploy_status, "
            "cf_deploy_status, publish_sha, last_event_at, updated_at"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(repo, pr_number) DO UPDATE SET "
            "title = excluded.title, head_sha = excluded.head_sha, "
            "head_ref = excluded.head_ref, html_url = excluded.html_url, "
            "reviewed = excluded.reviewed, open = excluded.open, "
            "ci_status = excluded.ci_status, merge_status = excluded.merge_status, "
            "publish_status = excluded.publish_status, "
            "m1_deploy_status = excluded.m1_deploy_status, "
            "cf_deploy_status = excluded.cf_deploy_status, "
            "publish_sha = excluded.publish_sha, "
            "last_event_at = excluded.last_event_at, updated_at = excluded.updated_at",
            (
                row.repo,
                row.pr_number,
                row.title,
                row.head_sha,
                row.head_ref,
                row.html_url,
                1 if row.reviewed else 0,
                1 if row.open else 0,
                row.ci_status,
                row.merge_status,
                row.publish_status,
                row.m1_deploy_status,
                row.cf_deploy_status,
                row.publish_sha,
                row.last_event_at,
                row.updated_at,
            ),
        )
        conn.execute(
            "DELETE FROM pipeline_checks WHERE repo = ? AND pr_number = ?",
            (row.repo, row.pr_number),
        )
        conn.executemany(
            "INSERT INTO pipeline_checks (repo, pr_number, name, status, conclusion) "
            "VALUES (?, ?, ?, ?, ?)",
            [
                (row.repo, row.pr_number, c.name, c.status, c.conclusion)
                for c in row.checks
            ],
        )
        conn.commit()

    def record_processed(self, trace_id: str, processed_at: str) -> None:
        conn = self._require_conn()
        conn.execute(
            "INSERT OR IGNORE INTO pipeline_processed (trace_id, processed_at) "
            "VALUES (?, ?)",
            (trace_id, processed_at),
        )
        conn.execute(
            "DELETE FROM pipeline_processed WHERE trace_id NOT IN ("
            "SELECT trace_id FROM pipeline_processed "
            "ORDER BY processed_at DESC LIMIT ?"
            ")",
            (_MAX_PROCESSED_ROWS,),
        )
        conn.commit()

    def set_last_publish_sha(self, sha: str) -> None:
        self.set_meta("last_publish_sha", sha)

    def set_meta(self, key: str, value: str) -> None:
        conn = self._require_conn()
        conn.execute(
            "INSERT INTO pipeline_meta (key, value) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
            (key, value),
        )
        conn.commit()

    def get_meta(self, key: str) -> str | None:
        conn = self._require_conn()
        row = conn.execute(
            "SELECT value FROM pipeline_meta WHERE key = ?",
            (key,),
        ).fetchone()
        if row is None:
            return None
        value = row["value"]
        return str(value) if value else None

    def _require_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("PipelineDb.connect() first")
        return self._conn