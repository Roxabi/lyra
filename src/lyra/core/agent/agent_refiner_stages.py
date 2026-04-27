"""Refinement stage functions for AgentRefiner.

Pure functions for building prompts and extracting patches from LLM responses.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.agent.agent_refiner import RefinementContext, RefinementPatch

__all__ = [
    "build_system_prompt",
    "extract_patch",
]


def build_system_prompt(ctx: "RefinementContext") -> str:
    """Build the system prompt for the LLM refiner session.

    Args:
        ctx: RefinementContext with agent profile snapshot.

    Returns:
        Formatted system prompt string for the LLM.
    """
    lines = [
        "You are an AI assistant helping an operator refine an AI agent profile.",
        "",
        f"Current profile for agent '{ctx.agent_name}':",
        f"  model: {ctx.model}",
    ]
    if ctx.persona_json:
        try:
            p = json.loads(ctx.persona_json)
            identity = p.get("identity", {})
            if identity.get("display_name"):
                lines.append(f"  display_name: {identity['display_name']}")
            if identity.get("role"):
                lines.append(f"  role: {identity['role']}")
        except json.JSONDecodeError:
            pass
    if ctx.voice_json:
        try:
            lines.append(f"  voice: {json.loads(ctx.voice_json)}")
        except json.JSONDecodeError:
            pass
    lines.extend(
        [
            f"  plugins: {ctx.plugins}",
            f"  passthroughs: {ctx.passthroughs}",
            "",
            "Your job:",
            "1. Present the current profile in plain language.",
            "2. Ask what the operator would like to change.",
            "3. Propose specific, concrete field updates.",
            "4. When the operator confirms changes (says 'yes', 'confirm', or 'done'),",
            "   output a summary followed by a JSON patch block:",
            "   <<PATCH>>",
            '   {"field_name": "new_value"}',
            "   <<END_PATCH>>",
            "",
            "Valid patchable AgentRow fields: model, persona_json,",
            "voice_json, patterns_json, passthroughs_json,",
            "plugins_json, fallback_language, tools_json, max_turns, streaming,",
            "memory_namespace, workspaces_json, commands_json.",
            "",
            "JSON field values must be valid strings"
            " (JSON arrays/objects as JSON strings).",
            "Be specific, concise, and helpful.",
        ]
    )
    return "\n".join(lines)


def extract_patch(text: str, patch_cls: type) -> "RefinementPatch | None":
    """Extract <<PATCH>>...<<END_PATCH>> block from LLM response.

    Args:
        text: Raw LLM response text.
        patch_cls: RefinementPatch class (injected to avoid circular import).

    Returns:
        RefinementPatch if valid block found, else None.
    """
    if "<<PATCH>>" not in text or "<<END_PATCH>>" not in text:
        return None
    try:
        raw = text.split("<<PATCH>>", 1)[1].split("<<END_PATCH>>", 1)[0].strip()
        fields = json.loads(raw)
        if not isinstance(fields, dict):
            return None
        return patch_cls(fields=fields)
    except (json.JSONDecodeError, IndexError, ValueError):
        return None
