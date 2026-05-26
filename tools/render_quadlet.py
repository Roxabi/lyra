#!/usr/bin/env python3
"""Render Quadlet .container files from .tmpl + ~/.lyra/config.toml.

Token in template: {{bot_secrets}}  →  one `Secret=` line per bot.
Bots come from config.toml [[auth.<platform>_bots]]; sorted by bot_id.
"""
from __future__ import annotations

import argparse
import os
import sys
import tempfile
import tomllib
from pathlib import Path

MARKER = "{{bot_secrets}}"


def load_config(path: Path) -> dict:
    try:
        with path.open("rb") as f:
            return tomllib.load(f)
    except FileNotFoundError:
        print(
            f"config file not found: {path}\n"
            "Run `lyra agent init` to bootstrap config.",
            file=sys.stderr,
        )
        sys.exit(1)
    except tomllib.TOMLDecodeError as exc:
        print(
            f"TOML parse error in {path}:\n  {exc}\n"
            "Fix the syntax error in the config file.",
            file=sys.stderr,
        )
        sys.exit(1)


def sort_bots(bots: list[dict]) -> list[dict]:
    return sorted(bots, key=lambda b: b["bot_id"])


def render_secrets(platform: str, bots: list[dict]) -> str:
    lines = [
        (
            f"Secret=lyra-bot-{platform}-{b['bot_id']},"
            f"type=mount,"
            f"target=bot_token-{b['bot_id']},"
            f"mode=0400,"
            f"uid=1500,"
            f"gid=1500"
        )
        for b in bots
    ]
    return "\n".join(lines)


def substitute(tmpl_text: str, secrets_block: str) -> str:
    if MARKER not in tmpl_text:
        raise ValueError(
            f"Template is missing the required marker `{MARKER}`.\n"
            "Add `{{bot_secrets}}` where bot Secret= lines should appear."
        )
    return tmpl_text.replace(MARKER, secrets_block)


def atomic_write(dest: Path, content: str) -> None:
    with tempfile.NamedTemporaryFile(
        mode="w", dir=dest.parent, prefix=f".{dest.name}.", delete=False
    ) as tmp:
        tmp.write(content)
        tmp_path = Path(tmp.name)
    os.replace(tmp_path, dest)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Render a Quadlet .container file from a template."
    )
    parser.add_argument("--platform", required=True, choices=["telegram", "discord"])
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--tmpl", required=True, type=Path)
    parser.add_argument("--dest", required=True, type=Path)
    args = parser.parse_args(argv)

    config = load_config(args.config)

    auth = config.get("auth", {})
    key = f"{args.platform}_bots"
    raw_bots = auth.get(key, [])
    bots = sort_bots(raw_bots)

    secrets_block = render_secrets(args.platform, bots)

    tmpl_text = args.tmpl.read_text()

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
