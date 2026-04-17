"""Tests for persona system prompt composition.

TOML-based persona loading was removed in #346 cleanup.
Persona data now lives inline in persona_json DB column.
"""

from __future__ import annotations

import pytest

from lyra.core.persona import compose_system_prompt_from_json


class TestComposeSystemPromptFromJson:
    def test_empty_returns_empty(self) -> None:
        assert compose_system_prompt_from_json({}) == ""

    def test_identity_only(self) -> None:
        result = compose_system_prompt_from_json(
            {"identity": {"display_name": "TestBot"}}
        )
        assert "You are TestBot." in result
        assert "Voice messages" in result  # always appended

    def test_full_persona(self) -> None:
        result = compose_system_prompt_from_json(
            {
                "identity": {
                    "display_name": "Lyra",
                    "tagline": "a personal AI assistant",
                    "creator": "Roxabi",
                    "goal": "Be helpful.",
                },
                "personality": {
                    "traits": ["direct", "precise"],
                    "style": "concise",
                    "tone": "professional",
                    "humor": "dry",
                },
                "expertise": {
                    "areas": ["Python", "testing"],
                    "instructions": ["Be thorough.", "Respond in English."],
                },
            }
        )
        assert "You are Lyra" in result
        assert "a personal AI assistant" in result
        assert "created by Roxabi" in result
        assert "Be helpful." in result
        assert "direct" in result
        assert "precise" in result
        assert "Python" in result
        assert "Be thorough." in result

    def test_voice_transcript_instruction_appended(self) -> None:
        result = compose_system_prompt_from_json({"identity": {"display_name": "Bot"}})
        assert "Voice messages" in result
        assert "voice_transcript" in result

    def test_size_guard(self) -> None:
        huge_instructions = ["x" * 1000] * 100
        with pytest.raises(ValueError, match="exceeds.*KB"):
            compose_system_prompt_from_json(
                {
                    "identity": {"display_name": "BigBot"},
                    "expertise": {"instructions": huge_instructions},
                }
            )

    def test_missing_sections_handled(self) -> None:
        result = compose_system_prompt_from_json(
            {"identity": {"display_name": "MinimalBot"}}
        )
        assert "You are MinimalBot." in result
        # Should not crash, just omit missing sections
