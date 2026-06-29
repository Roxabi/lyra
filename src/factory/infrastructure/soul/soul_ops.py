"""Soul document blobstore operations + write-through cache warming."""

from __future__ import annotations

import json
import logging
from collections.abc import Iterable
from typing import TYPE_CHECKING

from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache
from factory.core.persona import (
    compose_soul_document,
    compose_soul_document_from_markdown,
    merge_soul_sections,
    parse_soul_markdown,
    validate_soul_document_bytes,
)
from roxabi_contracts import BlobNotFoundError, BlobStoreServerError

if TYPE_CHECKING:
    from factory.core.ports.blobstore import BlobStorePort
    from factory.infrastructure.stores.registry.agent_store import AgentStore

log = logging.getLogger(__name__)

_SOUL_SOURCE = "soul"
_DEFAULT_META = {
    "schema_version": 1,
    "header": {"display_name": "", "tagline": ""},
    "memory": {
        "enabled": False,
        "namespace": None,
        "source": "cortex",
        "sync_mode": "none",
    },
    "extensions": {"cortex": {"entity_slug": None, "sync_mode": "none"}},
}


def default_soul_meta_json(display_name: str = "") -> str:
    meta = json.loads(json.dumps(_DEFAULT_META))
    meta["header"]["display_name"] = display_name
    return json.dumps(meta, separators=(",", ":"))


async def fetch_soul_markdown(
    blob_store: "BlobStorePort",
    store_key: str,
) -> str:
    data = await blob_store.get(store_key)
    return data.decode("utf-8")


def warm_soul_cache(
    agent_name: str,
    *,
    blob_ref: str | None,
    markdown: str,
) -> str:
    """Populate write-through cache; return composed prompt."""
    composed = compose_soul_document_from_markdown(markdown)
    get_soul_document_cache().put(
        agent_name,
        blob_ref=blob_ref,
        markdown=markdown,
        composed_prompt=composed,
    )
    return composed


async def preload_soul_caches_for_rows(
    rows: Iterable[AgentRow],
    blob_store: "BlobStorePort | None",
) -> None:
    """Fetch soul.md from blobstore and warm cache before agent_row_to_config."""
    if blob_store is None:
        return
    cache = get_soul_document_cache()
    for row in rows:
        ref = row.soul_document_blob_ref
        if not ref or cache.get(row.name, ref) is not None:
            continue
        try:
            markdown = await fetch_soul_markdown(blob_store, ref)
            warm_soul_cache(row.name, blob_ref=ref, markdown=markdown)
        except (
            BlobNotFoundError,
            BlobStoreServerError,
            UnicodeDecodeError,
            ValueError,
        ) as exc:
            log.warning(
                "preload_soul_cache(%s): failed ref=%s: %s",
                row.name,
                ref,
                exc,
            )


async def put_soul_document(
    blob_store: "BlobStorePort",
    agent_store: "AgentStore",
    agent_name: str,
    markdown: str,
    *,
    soul_meta_json: str | None = None,
) -> AgentRow:
    """PUT soul.md → PATCH agent row (atomic at hub layer)."""
    data = markdown.encode("utf-8")
    validate_soul_document_bytes(data)
    compose_soul_document_from_markdown(markdown)

    ref = await blob_store.put(
        data,
        mime="text/markdown",
        source=_SOUL_SOURCE,
        filename="soul.md",
    )
    store_key = ref.store_key
    if not store_key:
        raise ValueError("blobstore returned empty store_key")

    row = agent_store.get(agent_name)
    if row is None:
        raise KeyError(f"unknown agent: {agent_name!r}")

    # Drop stale ref-keyed entry before warm (cache.get matches blob_ref exactly).
    if row.soul_document_blob_ref and row.soul_document_blob_ref != store_key:
        get_soul_document_cache().invalidate(agent_name)
    warm_soul_cache(agent_name, blob_ref=store_key, markdown=markdown)

    updated = AgentRow(
        name=row.name,
        backend=row.backend,
        model=row.model,
        max_turns=row.max_turns,
        tools_json=row.tools_json,
        show_intermediate=row.show_intermediate,
        smart_routing_json=row.smart_routing_json,
        plugins_json=row.plugins_json,
        memory_namespace=row.memory_namespace,
        cwd=row.cwd,
        skip_permissions=row.skip_permissions,
        permissions_json=row.permissions_json,
        workspaces_json=row.workspaces_json,
        commands_json=row.commands_json,
        streaming=row.streaming,
        persona_json=row.persona_json,
        voice_json=row.voice_json,
        fallback_language=row.fallback_language,
        patterns_json=row.patterns_json,
        passthroughs_json=row.passthroughs_json,
        source=row.source,
        created_at=row.created_at,
        effort=row.effort,
        soul_meta_json=soul_meta_json or row.soul_meta_json or default_soul_meta_json(),
        soul_document_blob_ref=store_key,
        soul_document_bytes=len(data),
    )
    await agent_store.upsert(updated)
    log.info("soul.put agent=%s ref=%s bytes=%d", agent_name, store_key, len(data))
    return agent_store.get(agent_name) or updated


def parse_sections_for_editors(markdown: str) -> dict[str, str]:
    return parse_soul_markdown(markdown)


def preview_composed(sections: dict[str, str]) -> str:
    return compose_soul_document(sections)


def merge_and_validate_sections(sections: dict[str, str]) -> str:
    md = merge_soul_sections(sections)
    validate_soul_document_bytes(md.encode("utf-8"))
    compose_soul_document(sections)
    return md