"""Corpus sync helpers — canonical key, upserts, rate-limit logging.

This module holds pure helpers used by the V2 per-repo sync loop (T13) and
the V3 org-wide orchestrator (T20). No network I/O here — transport lives
in scripts.corpus.graphql.
"""
from __future__ import annotations

import re
import sqlite3
import sys
from datetime import datetime, timezone
from typing import Any

from scripts.corpus.graphql import ISSUES_QUERY, gh_graphql

_BARE_INT = re.compile(r"^\d+$")
_SHORT_FORM = re.compile(r"^#(\d+)$")
_FULL_KEY = re.compile(r"^[\w.-]+/[\w.-]+#\d+$")


def canonical_key(ref: int | str, repo: str) -> str:
    """Canonicalise an issue reference to 'owner/repo#N' form.

    - int 42 + 'Roxabi/lyra' → 'Roxabi/lyra#42'
    - '42' + 'Roxabi/lyra' → 'Roxabi/lyra#42'
    - '#9' + 'Roxabi/lyra' → 'Roxabi/lyra#9'
    - 'Roxabi/voiceCLI#7' + anything → 'Roxabi/voiceCLI#7' (pass-through)

    Raises ValueError on invalid input.
    """
    if isinstance(ref, int):
        return f"{repo}#{ref}"
    s = str(ref)
    if _FULL_KEY.match(s):
        return s
    m = _SHORT_FORM.match(s)
    if m:
        return f"{repo}#{m.group(1)}"
    if _BARE_INT.match(s):
        return f"{repo}#{s}"
    raise ValueError(f"Cannot canonicalise issue ref: {ref!r}")


def upsert_issue(conn: sqlite3.Connection, issue: dict[str, Any]) -> None:
    """INSERT OR REPLACE into issues. Expects keys:
    key, repo, number, title, state, url, created_at, updated_at,
    closed_at (nullable), milestone (nullable), is_stub (0 or 1).
    """
    conn.execute(
        """
        INSERT OR REPLACE INTO issues
            (key, repo, number, title, state, url, created_at, updated_at,
             closed_at, milestone, is_stub)
        VALUES
            (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            issue["key"],
            issue["repo"],
            issue["number"],
            issue["title"],
            issue["state"],
            issue["url"],
            issue["created_at"],
            issue["updated_at"],
            issue["closed_at"],
            issue["milestone"],
            issue["is_stub"],
        ),
    )


def upsert_labels(conn: sqlite3.Connection, issue_key: str, names: list[str]) -> None:
    """Wipe and rewrite labels for issue_key."""
    conn.execute("DELETE FROM labels WHERE issue_key = ?", (issue_key,))
    conn.executemany(
        "INSERT OR IGNORE INTO labels (issue_key, name) VALUES (?, ?)",
        [(issue_key, name) for name in names],
    )


def upsert_edges(
    conn: sqlite3.Connection,
    issue_key: str,
    blocked_by: list[str],
    blocking: list[str],
) -> None:
    """Wipe all edges touching issue_key (as src OR dst), then rewrite rows.

    Canonical direction:
    - Every blocker b in blocked_by → row (src=b, dst=issue_key).
    - Every blockee b in blocking → row (src=issue_key, dst=b).

    Idempotent. Duplicate (src, dst) pairs coalesce via PRIMARY KEY.
    All inputs are assumed already canonical — caller must use canonical_key first.
    """
    conn.execute(
        "DELETE FROM edges WHERE src_key = ? OR dst_key = ?",
        (issue_key, issue_key),
    )
    rows: list[tuple[str, str]] = []
    for blocker in blocked_by:
        rows.append((blocker, issue_key))
    for blockee in blocking:
        rows.append((issue_key, blockee))
    conn.executemany(
        "INSERT OR IGNORE INTO edges (src_key, dst_key) VALUES (?, ?)",
        rows,
    )


def log_rate_limit(rl: dict[str, Any]) -> None:
    print(
        f"[corpus] cost={rl['cost']} remaining={rl['remaining']} reset={rl['resetAt']}",
        file=sys.stderr,
    )


def run_repo_sync(
    conn: sqlite3.Connection,
    owner: str,
    name: str,
    since: str | None = None,
) -> dict[str, int]:
    """Paginate ISSUES_QUERY for owner/name, upsert all issues/labels/edges,
    log rate-limit per page, write sync_state row on success.

    Returns counts dict: {"pages": N, "issues": N}.
    """
    repo = f"{owner}/{name}"
    cursor: str | None = None
    pages = 0
    total_issues = 0

    while True:
        response = gh_graphql(
            ISSUES_QUERY,
            {"owner": owner, "name": name, "cursor": cursor, "since": since},
        )
        log_rate_limit(response["data"]["rateLimit"])

        issues_page = response["data"]["repository"]["issues"]
        nodes = issues_page["nodes"]

        for node in nodes:
            key = canonical_key(node["number"], repo)
            issue = {
                "key": key,
                "repo": repo,
                "number": node["number"],
                "title": node["title"],
                "state": node["state"].lower(),
                "url": node["url"],
                "created_at": node["createdAt"],
                "updated_at": node["updatedAt"],
                "closed_at": node["closedAt"],
                "milestone": (node["milestone"] or {}).get("title"),
                "is_stub": 0,
            }
            upsert_issue(conn, issue)

            labels = [n["name"] for n in node["labels"]["nodes"]]
            upsert_labels(conn, key, labels)

            blocking = [
                canonical_key(t["number"], t["repository"]["nameWithOwner"])
                for t in node["trackedIssues"]["nodes"]
            ]
            blocked_by = [
                canonical_key(t["number"], t["repository"]["nameWithOwner"])
                for t in node["trackedInIssues"]["nodes"]
            ]
            upsert_edges(conn, key, blocked_by, blocking)

        conn.commit()
        pages += 1
        total_issues += len(nodes)

        page_info = issues_page["pageInfo"]
        if page_info["hasNextPage"]:
            cursor = page_info["endCursor"]
        else:
            break

    now_iso = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "INSERT OR REPLACE INTO sync_state(repo, last_cursor, last_synced_at)"
        " VALUES (?, ?, ?)",
        (repo, None, now_iso),
    )
    conn.commit()

    return {"pages": pages, "issues": total_issues}
