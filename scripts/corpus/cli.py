"""Corpus CLI — unified entry point.

Subcommands:
  init    Initialise the corpus DB (idempotent).
  sync    Sync issues from GitHub (V2).
  stats   Print row counts from the corpus DB.

Common flags:
  --db PATH   (default: ~/.roxabi/corpus.db)
"""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from scripts.corpus import schema, sync
from scripts.corpus.graphql import GraphQLError

DEFAULT_DB = Path.home() / ".roxabi" / "corpus.db"


def cmd_init(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    schema.bootstrap(db_path)
    print(f"Initialised {db_path}")
    return 0


def cmd_sync(args: argparse.Namespace) -> int:
    if args.repo is None:
        print(
            "ERROR: --repo OWNER/NAME is required in V2 (org-wide sync lands in V3)",
            file=sys.stderr,
        )
        return 1
    if not re.match(r"^[\w.-]+/[\w.-]+$", args.repo):
        print(f"ERROR: --repo must be OWNER/NAME, got: {args.repo!r}", file=sys.stderr)
        return 1
    owner, name = args.repo.split("/", 1)
    db_path = Path(args.db)
    schema.bootstrap(db_path)
    conn = schema.connect(db_path)
    try:
        row = conn.execute(
            "SELECT last_synced_at FROM sync_state WHERE repo = ?",
            (f"{owner}/{name}",),
        ).fetchone()
        since = row[0] if row else None
        counts = sync.run_repo_sync(conn, owner, name, since=since)
    except FileNotFoundError:
        print(
            "ERROR: gh CLI not found or not authenticated — run `gh auth login`",
            file=sys.stderr,
        )
        return 2
    except GraphQLError as e:
        print(f"ERROR: GraphQL failure: {e}", file=sys.stderr)
        return 1
    finally:
        conn.close()
    print(
        f"Synced {counts['issues']} issues across {counts['pages']} pages"
        f" from {owner}/{name}"
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    db_path = Path(args.db)
    if not db_path.exists():
        print(f"ERROR: {db_path} not found — run `init` first", file=sys.stderr)
        return 1
    conn = schema.connect(db_path)
    try:
        issues = conn.execute("SELECT COUNT(*) FROM issues").fetchone()[0]
        labels = conn.execute("SELECT COUNT(*) FROM labels").fetchone()[0]
        edges = conn.execute("SELECT COUNT(*) FROM edges").fetchone()[0]
        repos = conn.execute("SELECT COUNT(*) FROM sync_state").fetchone()[0]
    finally:
        conn.close()
    print(f"issues={issues} labels={labels} edges={edges} repos={repos}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="corpus",
        description="Roxabi-org issue corpus sync.",
    )
    parser.add_argument(
        "--db",
        default=str(DEFAULT_DB),
        metavar="PATH",
        help="Path to corpus SQLite DB (default: %(default)s)",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    p_init = subparsers.add_parser("init", help="Initialise the corpus DB (idempotent)")
    p_init.set_defaults(func=cmd_init)

    p_sync = subparsers.add_parser("sync", help="Sync issues from GitHub (V2)")
    p_sync.add_argument("--repo", default=None, metavar="OWNER/NAME")
    p_sync.set_defaults(func=cmd_sync)

    p_stats = subparsers.add_parser("stats", help="Print row counts from the corpus DB")
    p_stats.set_defaults(func=cmd_stats)

    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
