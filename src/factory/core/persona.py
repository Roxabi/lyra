"""Persona loading and system prompt composition.

AgentSoul v1: five markdown sections in one ``soul.md`` blob, composed here only.
Legacy ``persona_json`` inline JSON is supported until Block 5 cleanup.
"""

from __future__ import annotations

import re

from factory.core.config.limits import MAX_PROMPT_BYTES as _MAX_PROMPT_BYTES
from factory.core.config.limits import MAX_SOUL_DOCUMENT_BYTES as _MAX_SOUL_DOC_BYTES

# Appended to every composed system prompt — infrastructure protocol, not persona.
_VOICE_TRANSCRIPT_INSTRUCTION = (
    "## Voice messages\n"
    "When the user's input is wrapped in <voice_transcript>…</voice_transcript>,"
    " the transcript has already been shown to the user as a confirmation."
    " Respond directly to the content — do not quote, repeat, or acknowledge"
    " the transcript itself."
)

SOUL_SECTION_ORDER: tuple[str, ...] = (
    "Identity",
    "Personality",
    "Values",
    "Expertise",
    "Guidelines",
)

_SOUL_HEADER_RE = re.compile(r"^##\s+(\w+)\s*$", re.MULTILINE)


def parse_soul_markdown(markdown: str) -> dict[str, str]:
    """Parse AgentSoul v1 markdown into section name → body (trimmed prose)."""
    if not markdown or not markdown.strip():
        return {}

    sections: dict[str, str] = {}
    current: str | None = None
    buf: list[str] = []

    def _flush() -> None:
        if current is not None:
            sections[current] = "\n".join(buf).strip()

    for line in markdown.splitlines():
        m = _SOUL_HEADER_RE.match(line.strip())
        if m:
            _flush()
            name = m.group(1)
            if name not in SOUL_SECTION_ORDER:
                raise ValueError(f"unknown soul section header: {name!r}")
            current = name
            buf = []
            continue
        if current is not None:
            buf.append(line)
    _flush()
    return sections


def merge_soul_sections(sections: dict[str, str]) -> str:
    """Merge section dict into canonical ``soul.md`` (fixed section order)."""
    parts: list[str] = []
    for name in SOUL_SECTION_ORDER:
        body = (sections.get(name) or "").strip()
        if body:
            parts.append(f"## {name}\n{body}")
    return "\n\n".join(parts) + ("\n" if parts else "")


def compose_soul_document(sections: dict[str, str]) -> str:
    """Compose opaque system prompt from AgentSoul v1 sections."""
    parts: list[str] = []
    for name in SOUL_SECTION_ORDER:
        body = (sections.get(name) or "").strip()
        if body:
            parts.append(f"## {name}\n{body}")
    parts.append(_VOICE_TRANSCRIPT_INSTRUCTION)
    composed = "\n\n".join(parts)

    encoded = composed.encode()
    if len(encoded) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"Composed system prompt exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded)} bytes)"
        )
    return composed


def compose_soul_document_from_markdown(markdown: str) -> str:
    """Parse markdown and compose system prompt in one step."""
    return compose_soul_document(parse_soul_markdown(markdown))


def validate_soul_document_bytes(data: bytes) -> None:
    """Reject soul.md payloads over the authoring cap."""
    if len(data) > _MAX_SOUL_DOC_BYTES:
        raise ValueError(
            f"soul document exceeds {_MAX_SOUL_DOC_BYTES // 1024}KB "
            f"limit ({len(data)} bytes)"
        )


def legacy_persona_json_to_sections(persona_dict: dict) -> dict[str, str]:  # noqa: C901
    """Map legacy ``persona_json`` dict to AgentSoul v1 sections for backfill."""
    if not persona_dict:
        return {}

    sections: dict[str, str] = {}
    ident = persona_dict.get("identity", {}) or {}
    identity_lines: list[str] = []
    if ident.get("display_name"):
        identity_lines.append(f"Display name: {ident['display_name']}")
    if ident.get("tagline"):
        identity_lines.append(f"Tagline: {ident['tagline']}")
    if ident.get("creator"):
        identity_lines.append(f"Creator: {ident['creator']}")
    if ident.get("goal"):
        identity_lines.append(ident["goal"])
    if identity_lines:
        sections["Identity"] = "\n".join(identity_lines)

    personality = persona_dict.get("personality", {}) or {}
    personality_lines: list[str] = []
    traits = personality.get("traits") or []
    if traits:
        personality_lines.append(f"Traits: {', '.join(traits)}")
    for key in ("style", "tone", "humor"):
        if personality.get(key):
            personality_lines.append(f"{key.capitalize()}: {personality[key]}")
    if personality_lines:
        sections["Personality"] = "\n".join(personality_lines)

    expertise = persona_dict.get("expertise", {}) or {}
    areas = expertise.get("areas") or []
    if areas:
        sections["Expertise"] = "Areas: " + ", ".join(areas)
    instructions = expertise.get("instructions") or []
    if instructions:
        sections["Guidelines"] = "\n".join(f"- {i}" for i in instructions)

    return sections


def compose_system_prompt_from_json(persona_dict: dict) -> str:  # noqa: C901 — DEBT:complexity-residual
    """Build system prompt from inline persona JSON (legacy DB column).

    Accepts the dict deserialized from ``agents.persona_json``.

    Returns empty string for empty/None input.
    """
    if not persona_dict:
        return ""

    parts: list[str] = []

    # Identity paragraph
    ident = persona_dict.get("identity", {})
    display_name = ident.get("display_name", "")
    if display_name:
        intro = f"You are {display_name}"
        tagline = ident.get("tagline", "")
        if tagline:
            intro += f", {tagline}"
        creator = ident.get("creator", "")
        if creator:
            intro += f", created by {creator}"
        intro += "."
        goal = ident.get("goal", "")
        if goal:
            intro += f" {goal}"
        parts.append(intro)

    # Personality paragraph
    personality = persona_dict.get("personality", {})
    traits = personality.get("traits", [])
    style = personality.get("style", "")
    tone = personality.get("tone", "")
    humor = personality.get("humor", "")
    if traits or style or tone:
        personality_parts: list[str] = []
        if traits:
            personality_parts.append(f"Your core traits are: {', '.join(traits)}.")
        if style:
            personality_parts.append(f"Your communication style is {style}.")
        if tone:
            personality_parts.append(f"Your tone is {tone}.")
        if humor:
            personality_parts.append(f"Your sense of humor: {humor}.")
        parts.append(" ".join(personality_parts))

    # Expertise paragraph
    expertise = persona_dict.get("expertise", {})
    areas = expertise.get("areas", [])
    instructions = expertise.get("instructions", [])
    if areas:
        parts.append(f"Your areas of expertise include: {', '.join(areas)}.")
    if instructions:
        instruction_lines = "\n".join(f"- {i}" for i in instructions)
        parts.append(f"Guidelines:\n{instruction_lines}")

    parts.append(_VOICE_TRANSCRIPT_INSTRUCTION)
    composed = "\n\n".join(parts)

    encoded = composed.encode()
    if len(encoded) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"Composed system prompt exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded)} bytes)"
        )

    return composed