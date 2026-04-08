"""Persona loading and system prompt composition.

load_persona() and compose_system_prompt() are kept for the _populate_343
migration in AgentStore (upgrading old DBs). New code should use
compose_system_prompt_from_json() which reads from persona_json.
"""

from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

_MAX_PROMPT_BYTES = 64 * 1024  # 64 KB

# Appended to every composed system prompt — infrastructure protocol, not persona.
_VOICE_TRANSCRIPT_INSTRUCTION = (
    "## Voice messages\n"
    "When the user's input is wrapped in <voice_transcript>…</voice_transcript>,"
    " the transcript has already been shown to the user as a confirmation."
    " Respond directly to the content — do not quote, repeat, or acknowledge"
    " the transcript itself."
)

_VAULT_DIR = Path(
    os.environ.get("ROXABI_VAULT_DIR", str(Path.home() / ".roxabi-vault"))
)
_PERSONAS_DIR = _VAULT_DIR / "personas"


# ---------------------------------------------------------------------------
# Persona dataclasses (kept for migration; not used by new code)
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class IdentityConfig:
    name: str
    tagline: str = ""
    creator: str = ""
    role: str = ""
    goal: str = ""


@dataclass(frozen=True)
class PersonalityConfig:
    traits: tuple[str, ...] = ()
    communication_style: str = ""
    tone: str = ""
    humor: str = ""


@dataclass(frozen=True)
class ExpertiseConfig:
    areas: tuple[str, ...] = ()
    instructions: tuple[str, ...] = ()


@dataclass(frozen=True)
class VoiceConfig:
    speaking_style: str = ""
    pace: str = ""
    warmth: str = ""


@dataclass(frozen=True)
class PersonaConfig:
    identity: IdentityConfig
    personality: PersonalityConfig = field(default_factory=PersonalityConfig)
    expertise: ExpertiseConfig = field(default_factory=ExpertiseConfig)
    voice: VoiceConfig = field(default_factory=VoiceConfig)


# ---------------------------------------------------------------------------
# TOML-based persona loading (kept for _populate_343 migration)
# ---------------------------------------------------------------------------


def load_persona(name: str, personas_dir: Path | None = None) -> PersonaConfig:
    """Load PersonaConfig from a TOML file in the vault.

    Resolves name to {personas_dir}/{name}.persona.toml.
    Validates: name safe, file exists, [identity].name present.
    Creates personas_dir if absent.
    """
    if not re.match(r"^[a-zA-Z0-9_-]+$", name):
        raise ValueError(f"Invalid persona name {name!r}: only [a-zA-Z0-9_-] allowed")

    directory = personas_dir or _PERSONAS_DIR
    directory.mkdir(parents=True, exist_ok=True)

    path = directory / f"{name}.persona.toml"
    if not path.resolve().is_relative_to(directory.resolve()):
        raise ValueError(f"Persona name {name!r} escapes personas directory")
    if not path.exists():
        raise FileNotFoundError(f"Persona config not found: {path}")

    with path.open("rb") as f:
        data = tomllib.load(f)

    identity_data = data.get("identity", {})
    if not identity_data.get("name"):
        raise ValueError(f"Persona {name!r} missing required [identity].name")

    identity = IdentityConfig(
        name=identity_data["name"],
        tagline=identity_data.get("tagline", ""),
        creator=identity_data.get("creator", ""),
        role=identity_data.get("role", ""),
        goal=identity_data.get("goal", ""),
    )

    personality_data = data.get("personality", {})
    personality = PersonalityConfig(
        traits=tuple(personality_data.get("traits", [])),
        communication_style=personality_data.get("communication_style", ""),
        tone=personality_data.get("tone", ""),
        humor=personality_data.get("humor", ""),
    )

    expertise_data = data.get("expertise", {})
    expertise = ExpertiseConfig(
        areas=tuple(expertise_data.get("areas", [])),
        instructions=tuple(expertise_data.get("instructions", [])),
    )

    voice_data = data.get("voice", {})
    voice = VoiceConfig(
        speaking_style=voice_data.get("speaking_style", ""),
        pace=voice_data.get("pace", ""),
        warmth=voice_data.get("warmth", ""),
    )

    return PersonaConfig(
        identity=identity,
        personality=personality,
        expertise=expertise,
        voice=voice,
    )


def compose_system_prompt(persona: PersonaConfig) -> str:  # noqa: C901 — many optional persona fields each add one branch
    """Build a natural prose system prompt from PersonaConfig fields."""
    parts: list[str] = []

    # Identity paragraph
    ident = persona.identity
    intro = f"You are {ident.name}"
    if ident.tagline:
        intro += f", {ident.tagline}"
    if ident.creator:
        intro += f", created by {ident.creator}"
    intro += "."
    if ident.goal:
        intro += f" {ident.goal}"
    parts.append(intro)

    # Personality paragraph
    p = persona.personality
    if p.traits or p.communication_style or p.tone:
        personality_parts: list[str] = []
        if p.traits:
            personality_parts.append(f"Your core traits are: {', '.join(p.traits)}.")
        if p.communication_style:
            personality_parts.append(
                f"Your communication style is {p.communication_style}."
            )
        if p.tone:
            personality_parts.append(f"Your tone is {p.tone}.")
        if p.humor:
            personality_parts.append(f"Your sense of humor: {p.humor}.")
        parts.append(" ".join(personality_parts))

    # Expertise paragraph
    e = persona.expertise
    if e.areas:
        parts.append(f"Your areas of expertise include: {', '.join(e.areas)}.")
    if e.instructions:
        instruction_lines = "\n".join(f"- {i}" for i in e.instructions)
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


# ---------------------------------------------------------------------------
# JSON-based prompt composition (primary path after #343)
# ---------------------------------------------------------------------------


def compose_system_prompt_from_json(persona_dict: dict) -> str:  # noqa: C901
    """Build system prompt from inline persona JSON (DB persona_json column).

    Accepts the dict deserialized from ``agents.persona_json``.
    Key mapping mirrors ``compose_system_prompt(PersonaConfig)``::

        PersonaConfig field           → persona_json key
        identity.name                 → identity.display_name
        identity.creator              → identity.creator
        identity.goal                 → identity.goal
        personality.communication_style → personality.style
        personality.tone              → personality.tone

    Returns empty string for empty/None input.
    """
    if not persona_dict:
        return ""

    parts: list[str] = []

    # Identity paragraph (mirrors compose_system_prompt)
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
