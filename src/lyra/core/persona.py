"""Persona loading and system prompt composition.

compose_system_prompt_from_json() reads persona_json from the DB and builds
the system prompt. Legacy TOML-based loading was removed in #346 cleanup.
"""

from __future__ import annotations

_MAX_PROMPT_BYTES = 64 * 1024  # 64 KB

# Appended to every composed system prompt — infrastructure protocol, not persona.
_VOICE_TRANSCRIPT_INSTRUCTION = (
    "## Voice messages\n"
    "When the user's input is wrapped in <voice_transcript>…</voice_transcript>,"
    " the transcript has already been shown to the user as a confirmation."
    " Respond directly to the content — do not quote, repeat, or acknowledge"
    " the transcript itself."
)


def compose_system_prompt_from_json(persona_dict: dict) -> str:  # noqa: C901
    """Build system prompt from inline persona JSON (DB persona_json column).

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
