"""Hub-side NATS RPC handlers for dashboard agent + soul config."""

from __future__ import annotations

import json
import logging
from typing import TYPE_CHECKING, Any

from factory.core.agent.agent_models import AgentRow
from factory.core.agent.soul_cache import get_soul_document_cache
from factory.infrastructure.soul.soul_ops import (
    default_soul_meta_json,
    fetch_soul_markdown,
    preview_composed,
    put_soul_document,
    warm_soul_cache,
)
from roxabi_contracts.dashboard import (
    DashboardAgentConfigResponse,
    DashboardAgentPatchRequest,
    DashboardAgentsListResponse,
    DashboardAgentSoulPreviewRequest,
    DashboardAgentSoulPreviewResponse,
    DashboardAgentSoulPutRequest,
    DashboardAgentSoulSectionsResponse,
    DashboardAgentSummary,
    SoulMetaEnvelope,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

    from factory.core.hub import Hub

log = logging.getLogger(__name__)

_PREVIEW_TRUNCATE = 8192


def _agent_store(hub: Hub):
    store = getattr(hub, "_agent_store", None)
    if store is None:
        agent = next(iter(hub.agent_registry.values()), None)
        store = getattr(agent, "_agent_store", None) if agent else None
    return store


def _blob_store(hub: Hub):
    return getattr(hub, "_blob_store", None)


async def _soul_markdown_for_row(hub: Hub, row: AgentRow) -> str:
    cache = get_soul_document_cache()
    if row.soul_document_blob_ref:
        entry = cache.get(row.name, row.soul_document_blob_ref)
        if entry is not None:
            return entry.markdown
        blob = _blob_store(hub)
        if blob is not None:
            md = await fetch_soul_markdown(blob, row.soul_document_blob_ref)
            warm_soul_cache(row.name, blob_ref=row.soul_document_blob_ref, markdown=md)
            return md
        log.warning(
            "soul.get(%s): blobstore unavailable ref=%s — persona_json fallback",
            row.name,
            row.soul_document_blob_ref,
        )
    return ""


def _legacy_persona_sections(row: AgentRow) -> dict[str, str]:
    if not row.persona_json:
        return {}
    try:
        from factory.core.persona import legacy_persona_json_to_sections

        persona = json.loads(row.persona_json)
        return legacy_persona_json_to_sections(persona)
    except (json.JSONDecodeError, TypeError, ValueError):
        log.warning("soul.get(%s): invalid persona_json — ignored", row.name)
        return {}


async def _soul_sections_for_row(hub: Hub, row: AgentRow) -> dict[str, str]:
    from factory.core.persona import parse_soul_markdown

    md = await _soul_markdown_for_row(hub, row)
    if md:
        return parse_soul_markdown(md)
    return _legacy_persona_sections(row)


def _meta_envelope(row: AgentRow) -> SoulMetaEnvelope | None:
    raw = row.soul_meta_json
    if not raw:
        return None
    try:
        return SoulMetaEnvelope.model_validate(json.loads(raw))
    except (json.JSONDecodeError, TypeError, ValueError):
        return None


async def handle_agents_list(hub: Hub, _nc: NATS, _payload: dict[str, Any]) -> dict:
    store = _agent_store(hub)
    if store is None:
        return DashboardAgentsListResponse(agents=[]).model_dump()
    agents = [
        DashboardAgentSummary(
            name=r.name,
            backend=r.backend,  # type: ignore[arg-type]
            model=r.model,
            updated_at=r.updated_at,
            soul_document_bytes=r.soul_document_bytes,
            has_soul=bool(r.soul_document_blob_ref),
        )
        for r in store.get_all()
    ]
    return DashboardAgentsListResponse(agents=agents).model_dump()


async def handle_agents_get(hub: Hub, _nc: NATS, payload: dict[str, Any]) -> dict:
    name = str(payload.get("name") or "")
    store = _agent_store(hub)
    if store is None or not name:
        return {"error": "not_found"}
    row = store.get(name)
    if row is None:
        return {"error": "not_found"}
    voice = json.loads(row.voice_json) if row.voice_json else None
    return DashboardAgentConfigResponse(
        name=row.name,
        backend=row.backend,  # type: ignore[arg-type]
        model=row.model,
        voice_json=voice,
        soul_meta_json=_meta_envelope(row),
        soul_document_blob_ref=row.soul_document_blob_ref,
        soul_document_bytes=row.soul_document_bytes,
        updated_at=row.updated_at,
    ).model_dump()


async def handle_agents_patch(hub: Hub, _nc: NATS, payload: dict[str, Any]) -> dict:
    name = str(payload.get("name") or "")
    req = DashboardAgentPatchRequest.model_validate(payload.get("patch") or payload)
    store = _agent_store(hub)
    if store is None or not name:
        return {"error": "not_found"}
    row = store.get(name)
    if row is None:
        return {"error": "not_found"}

    meta_raw = row.soul_meta_json or default_soul_meta_json()
    meta = json.loads(meta_raw)
    if req.display_name is not None:
        meta.setdefault("header", {})["display_name"] = req.display_name
    if req.tagline is not None:
        meta.setdefault("header", {})["tagline"] = req.tagline

    updated = AgentRow(
        name=row.name,
        backend=req.backend or row.backend,
        model=req.model or row.model,
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
        voice_json=(
            json.dumps(req.voice_json)
            if req.voice_json is not None
            else row.voice_json
        ),
        fallback_language=row.fallback_language,
        patterns_json=row.patterns_json,
        passthroughs_json=row.passthroughs_json,
        source=row.source,
        created_at=row.created_at,
        effort=row.effort,
        soul_meta_json=json.dumps(meta),
        soul_document_blob_ref=row.soul_document_blob_ref,
        soul_document_bytes=row.soul_document_bytes,
    )
    await store.upsert(updated)
    return await handle_agents_get(hub, _nc, {"name": name})


async def handle_agents_soul_put(hub: Hub, _nc: NATS, payload: dict[str, Any]) -> dict:
    name = str(payload.get("name") or "")
    req = DashboardAgentSoulPutRequest.model_validate(payload.get("body") or payload)
    store = _agent_store(hub)
    blob = _blob_store(hub)
    if store is None or blob is None:
        return {"error": "blobstore_unavailable"}
    if not name or store.get(name) is None:
        return {"error": "not_found"}
    try:
        await put_soul_document(blob, store, name, req.markdown)
    except ValueError as exc:
        return {"error": "validation", "message": str(exc)}
    return await handle_agents_soul_get(hub, _nc, {"name": name})


async def handle_agents_soul_get(hub: Hub, _nc: NATS, payload: dict[str, Any]) -> dict:
    name = str(payload.get("name") or "")
    store = _agent_store(hub)
    if store is None or not name:
        return {"error": "not_found"}
    row = store.get(name)
    if row is None:
        return {"error": "not_found"}
    sections = await _soul_sections_for_row(hub, row)
    return DashboardAgentSoulSectionsResponse(
        sections=sections,
        soul_document_blob_ref=row.soul_document_blob_ref,
        soul_document_bytes=row.soul_document_bytes,
        updated_at=row.updated_at,
    ).model_dump()


async def handle_agents_soul_preview(
    hub: Hub, _nc: NATS, payload: dict[str, Any]
) -> dict:
    req = DashboardAgentSoulPreviewRequest.model_validate(payload)
    composed = preview_composed(req.sections)
    truncated = len(composed) > _PREVIEW_TRUNCATE
    if truncated:
        composed = composed[:_PREVIEW_TRUNCATE]
    return DashboardAgentSoulPreviewResponse(
        composed=composed,
        truncated=truncated,
    ).model_dump()