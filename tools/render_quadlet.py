#!/usr/bin/env python3
"""Render Quadlet .container files from .tmpl + BotStore.

Token in template: {{bot_secrets}}  →  one `Secret=` line per bot.
Bots come from BotStore (~/.lyra/config.db); sorted by bot_id.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import re
import sqlite3
import sys
import tempfile
from pathlib import Path

from lyra.infrastructure.stores.bot_store import BotStore

MARKER = "{{bot_secrets}}"
_BOT_ID_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,63}$")


def validate_bot_id(bot_id: str, platform: str) -> None:
    if not _BOT_ID_RE.fullmatch(bot_id):
        print(
            f"invalid bot_id {bot_id!r} for platform {platform}: "
            "must match [a-z0-9][a-z0-9_-]{0,63}",
            file=sys.stderr,
        )
        sys.exit(1)


def sort_bots(bots: list) -> list:
    return sorted(bots, key=lambda b: b.bot_id)


def render_secrets(platform: str, bots: list) -> str:
    lines: list[str] = []
    for bot in bots:
        bot_id = bot.bot_id
        lines.append(
            f"Secret=lyra-bot-{platform}-{bot_id},"
            f"type=mount,"
            f"target=bot_token-{bot_id},"
            f"mode=0400,"
            f"uid=1500,"
            f"gid=1500"
        )
        if bot.webhook_enabled is True:
            lines.append(
                f"Secret=lyra-bot-{platform}-{bot_id}-webhook,"
                f"type=mount,"
                f"target=bot_webhook-{bot_id},"
                f"mode=0400,"
                f"uid=1500,"
                f"gid=1500"
            )
    return "\n".join(lines)


def substitute(tmpl_text: str, secrets_block: str) -> str:
    if MARKER not in tmpl_text:
        raise ValueError(
            f"Template is missing the required marker `{MARKER}`.\n"
            "Add `{{bot_secrets}}` where bot Secret= lines should appear."
        )
    return tmpl_text.replace(MARKER, secrets_block)


def atomic_write(dest: Path, content: str) -> None:
    tmp_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", dir=dest.parent, prefix=f".{dest.name}.", delete=False
        ) as tmp:
            tmp_path = Path(tmp.name)
            tmp.write(content)
            tmp.flush()
            os.fsync(tmp.fileno())
        os.replace(tmp_path, dest)
    except Exception:
        if tmp_path is not None:
            tmp_path.unlink(missing_ok=True)
        raise


async def _load_bots(db_path: Path, platform: str) -> list:
    try:
        store = BotStore(db_path=str(db_path))
        await store.connect()
    except (OSError, sqlite3.Error) as exc:
        print(
            f"Failed to open BotStore at {db_path}:\n  {exc}\n"
            "Run `lyra bot init` to seed the bot database.",
            file=sys.stderr,
        )
        sys.exit(1)

    all_bots = store.get_all()
    platform_bots = [b for b in all_bots if b.platform == platform]
    await store.close()
    return platform_bots


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a Quadlet .container file from a template."
    )
    parser.add_argument("--platform", required=True, choices=["telegram", "discord"])
    parser.add_argument("--db", required=True, type=Path)
    parser.add_argument("--tmpl", required=True, type=Path)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args(argv)

    if not args.db.exists():
        print(
            f"bot database not found: {args.db}\n"
            "Run `lyra bot init` to seed the bot database.",
            file=sys.stderr,
        )
        sys.exit(1)

    bots = asyncio.run(_load_bots(args.db, args.platform))

    for bot in bots:
        validate_bot_id(bot.bot_id, args.platform)

    bots = sort_bots(bots)

    secrets_block = render_secrets(args.platform, bots)

    try:
        tmpl_text = args.tmpl.read_text()
    except FileNotFoundError:
        print(f"template file not found: {args.tmpl}", file=sys.stderr)
        sys.exit(1)

    try:
        rendered = substitute(tmpl_text, secrets_block)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    args.dest.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(args.dest, rendered)
    return 0


if __name__ == "__main__":
    sys.exit(main())
