#!/usr/bin/env python3
"""One-shot migration: bot_secrets (SQLite) → per-bot Podman secrets (#1057).

Run once on the production host (M₁) AFTER pulling the staging branch that
deletes CredentialStore. Operators on machines that never seeded a
config.db bot_secrets table can skip this script entirely.

The script is intentionally self-contained — it does NOT import from the
deleted `lyra.infrastructure.stores.credential_store` module. It reads
`~/.lyra/config.db` directly via the stdlib sqlite3 module and decrypts
each row with the existing Fernet keyring at `~/.lyra/keyring.key`.

After a successful run:
  1. Inspect: `podman secret ls --filter name=lyra-bot-`
  2. Generate the Quadlet fragment: `make quadlet-bot-secrets-render`
  3. Paste the per-platform fragments into the matching .container files.
  4. Restart adapters: `systemctl --user restart lyra-telegram lyra-discord`
  5. Optional cleanup: `sqlite3 ~/.lyra/config.db 'DROP TABLE bot_secrets'`

The script is idempotent (Podman secret creates use --replace) and safe to
re-run if interrupted.

Usage::

    python3 tools/migrate_bot_secrets_to_podman.py
    python3 tools/migrate_bot_secrets_to_podman.py --vault /custom/path
    python3 tools/migrate_bot_secrets_to_podman.py --dry-run

Exit codes:
  0 — success (all rows migrated)
  1 — config.db / keyring missing or unreadable
  2 — decryption failure (corrupted keyring or DB)
  3 — podman secret create failure
"""

from __future__ import annotations

import argparse
import sqlite3
import subprocess
import sys
from pathlib import Path

try:
    from cryptography.fernet import Fernet, InvalidToken
except ImportError as exc:
    sys.exit(f"cryptography not installed: {exc}")


def _load_keyring(key_path: Path) -> Fernet:
    if not key_path.exists():
        sys.exit(f"Keyring not found at {key_path}")
    try:
        key = key_path.read_bytes()
    except PermissionError as exc:
        sys.exit(f"Keyring at {key_path} is not readable: {exc}")
    try:
        return Fernet(key)
    except ValueError as exc:
        sys.exit(f"Invalid keyring at {key_path}: {exc}")


def _read_rows(db_path: Path) -> list[tuple[str, str, str, str | None]]:
    if not db_path.exists():
        sys.exit(f"config.db not found at {db_path} — nothing to migrate")
    conn = sqlite3.connect(str(db_path))
    try:
        cursor = conn.execute("PRAGMA table_info(bot_secrets)")
        cols = {row[1] for row in cursor.fetchall()}
        if "platform" not in cols or "bot_id" not in cols or "token" not in cols:
            sys.exit(
                f"{db_path}: bot_secrets table missing or has unexpected schema — "
                "nothing to migrate"
            )
        cursor = conn.execute(
            "SELECT platform, bot_id, token, webhook_secret FROM bot_secrets"
        )
        return list(cursor.fetchall())
    finally:
        conn.close()


def _podman_secret_create(name: str, content: bytes, *, dry_run: bool) -> None:
    if dry_run:
        print(f"[dry-run] would create podman secret: {name}")
        return
    try:
        subprocess.run(
            ["podman", "secret", "create", "--replace", name, "-"],
            input=content,
            check=True,
        )
    except subprocess.CalledProcessError as exc:
        sys.exit(f"podman secret create {name} failed (exit {exc.returncode})")
    except FileNotFoundError:
        sys.exit("podman binary not found in PATH")


def main(argv: list[str] | None = None) -> int:
    description = (__doc__ or "").split("\n\n", 1)[0]
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument(
        "--vault",
        type=Path,
        default=Path.home() / ".lyra",
        help="Vault directory containing config.db + keyring.key (default: ~/.lyra)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print actions without creating Podman secrets",
    )
    args = parser.parse_args(argv)

    vault: Path = args.vault
    fernet = _load_keyring(vault / "keyring.key")
    rows = _read_rows(vault / "config.db")

    if not rows:
        print("bot_secrets table empty — nothing to migrate")
        return 0

    tokens = 0
    webhooks = 0
    skipped: list[str] = []
    for platform, bot_id, enc_token, enc_webhook in rows:
        try:
            token = fernet.decrypt(enc_token.encode()).decode()
        except InvalidToken:
            skipped.append(f"{platform}/{bot_id}: token decryption failed")
            continue
        _podman_secret_create(
            f"lyra-bot-{platform}-{bot_id}",
            token.encode(),
            dry_run=args.dry_run,
        )
        tokens += 1
        if enc_webhook is not None:
            try:
                webhook = fernet.decrypt(enc_webhook.encode()).decode()
            except InvalidToken:
                skipped.append(f"{platform}/{bot_id}: webhook decryption failed")
                continue
            _podman_secret_create(
                f"lyra-bot-{platform}-{bot_id}-webhook",
                webhook.encode(),
                dry_run=args.dry_run,
            )
            webhooks += 1
        print(f"{platform} {bot_id} OK ({'webhook' if enc_webhook else 'no-webhook'})")

    print(
        f"\nMigrated {tokens} token(s), {webhooks} webhook secret(s)."
        f" {'DRY-RUN — no secrets created.' if args.dry_run else ''}"
    )
    if skipped:
        print("Skipped:", file=sys.stderr)
        for line in skipped:
            print(f"  {line}", file=sys.stderr)
        return 2
    if not args.dry_run:
        print(
            "\nNext steps:\n"
            "  1. podman secret ls --filter name=lyra-bot-\n"
            "  2. make quadlet-bot-secrets-render\n"
            "  3. paste the .bot-secrets.{telegram,discord}.fragment lines "
            "into the matching .container files\n"
            "  4. systemctl --user restart lyra-telegram lyra-discord\n"
            "  5. (optional) sqlite3 ~/.lyra/config.db 'DROP TABLE bot_secrets'"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
