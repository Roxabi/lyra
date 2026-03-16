from __future__ import annotations

import os
import re
import tomllib
from pathlib import Path

from .agent_config import (
    _MAX_PROMPT_BYTES,
    ExpertiseConfig,
    IdentityConfig,
    PersonaConfig,
    PersonalityConfig,
    VoiceConfig,
)

_VAULT_DIR = Path(
    os.environ.get("ROXABI_VAULT_DIR", str(Path.home() / ".roxabi-vault"))
)
_PERSONAS_DIR = _VAULT_DIR / "personas"


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

    composed = "\n\n".join(parts)

    encoded = composed.encode()
    if len(encoded) > _MAX_PROMPT_BYTES:
        raise ValueError(
            f"Composed system prompt exceeds {_MAX_PROMPT_BYTES // 1024}KB "
            f"limit ({len(encoded)} bytes)"
        )

    return composed
