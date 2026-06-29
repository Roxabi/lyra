#!/usr/bin/env python3
"""One-shot backfill: persona_json → soul.md blob + DB ref (idempotent)."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
from pathlib import Path

from factory.bootstrap.factory.voice_overlay import init_blobstore
from factory.core.persona import legacy_persona_json_to_sections, merge_soul_sections
from factory.infrastructure.soul.soul_ops import (
    default_soul_meta_json,
    put_soul_document,
)
from factory.infrastructure.stores.registry.agent_store import AgentStore
from factory.paths import factory_data_dir

log = logging.getLogger(__name__)


async def _run(db_path: Path, *, dry_run: bool) -> int:
    blob = init_blobstore()
    if blob is None:
        log.error("blobstore unavailable — configure FACTORY_BLOBSTORE_TOKEN_PATH")
        return 1
    store = AgentStore(db_path)
    await store.connect()
    migrated = skipped = errors = 0
    try:
        for row in store.get_all():
            if row.soul_document_blob_ref:
                skipped += 1
                continue
            if not row.persona_json:
                skipped += 1
                continue
            try:
                persona = json.loads(row.persona_json)
                sections = legacy_persona_json_to_sections(persona)
                if not sections:
                    skipped += 1
                    continue
                md = merge_soul_sections(sections)
                ident = persona.get("identity") or {}
                display = ident.get("display_name") or row.name
                meta = default_soul_meta_json(str(display))
                if dry_run:
                    log.info("would migrate %s (%d bytes)", row.name, len(md.encode()))
                    migrated += 1
                    continue
                await put_soul_document(blob, store, row.name, md, soul_meta_json=meta)
                migrated += 1
            except (json.JSONDecodeError, ValueError, KeyError):
                log.exception("failed agent %s", row.name)
                errors += 1
    finally:
        await store.close()
    log.info("done migrated=%d skipped=%d errors=%d", migrated, skipped, errors)
    return 0 if errors == 0 else 1


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--db",
        type=Path,
        default=factory_data_dir() / "config.db",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_run(args.db, dry_run=args.dry_run)))


if __name__ == "__main__":
    main()