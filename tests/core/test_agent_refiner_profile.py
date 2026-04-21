"""Tests for AgentRefiner read_profile, apply_patch, RefinementPatch, and validation."""

from __future__ import annotations

import asyncio
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent.agent_models import AgentRow
from lyra.core.agent.agent_refiner import (
    AgentRefiner,
    RefinementContext,
    RefinementPatch,
)


@pytest.fixture(autouse=True)
def _restore_event_loop():  # noqa: F841  # autouse fixture
    """Restore a fresh event loop after each test."""
    yield
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)


def make_row(**kwargs) -> AgentRow:
    """Minimal AgentRow for testing."""
    defaults: dict[str, Any] = dict(
        name="lyra_default",
        backend="anthropic-sdk",
        model="claude-haiku-4-5-20251001",
        persona_json='{"identity": {"display_name": "Lyra"}}',
        voice_json='{"tts": {"voice": "echo"}, "stt": {}}',
        plugins_json='["plugin_a"]',
        patterns_json='{"bare_url": true}',
    )
    defaults.update(kwargs)
    return AgentRow(**defaults)


def make_store(row: AgentRow | None = None) -> MagicMock:
    store = MagicMock()
    store.get.return_value = row
    store.upsert = AsyncMock()
    store.close = AsyncMock()
    return store


# ---------------------------------------------------------------------------
# T1 — read_profile (SC-1)
# ---------------------------------------------------------------------------


class TestReadProfile:
    """AgentRefiner.read_profile() — builds RefinementContext from store."""

    def test_read_profile_returns_correct_context(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert
        assert ctx.agent_name == "lyra_default"
        assert ctx.model == "claude-haiku-4-5-20251001"
        assert ctx.persona_json == '{"identity": {"display_name": "Lyra"}}'
        assert ctx.plugins == ["plugin_a"]
        assert ctx.patterns == {"bare_url": True}

    def test_read_profile_raises_on_missing_agent(self) -> None:
        # Arrange
        store = make_store(row=None)
        refiner = AgentRefiner("missing", store)

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.read_profile()

    def test_read_profile_handles_null_plugins_json(self) -> None:
        # Arrange — plugins_json defaults to "[]" in AgentRow
        row = make_row(plugins_json="[]", patterns_json=None)
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert
        assert ctx.plugins == []
        assert ctx.patterns == {}

    def test_read_profile_parses_voice_json(self) -> None:
        # Arrange
        row = make_row(
            voice_json='{"tts": {"voice": "echo", "engine": "qwen-fast"}, "stt": {}}'
        )
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert -- voice_json is passed through as raw string
        assert ctx.voice_json == (
            '{"tts": {"voice": "echo", "engine": "qwen-fast"}, "stt": {}}'
        )

    def test_read_profile_returns_frozen_context(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)

        # Act
        ctx = refiner.read_profile()

        # Assert — RefinementContext is frozen
        assert isinstance(ctx, RefinementContext)
        with pytest.raises(AttributeError):
            setattr(ctx, "agent_name", "new_name")


# ---------------------------------------------------------------------------
# T2 — apply_patch / RefinementPatch (SC-1)
# ---------------------------------------------------------------------------


class TestRefinementPatch:
    """RefinementPatch — as_json() and to_agent_row() behaviour."""

    def test_as_json_returns_valid_json_string(self) -> None:
        # Arrange
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        result = patch.as_json()

        # Assert
        parsed = json.loads(result)
        assert parsed == {"model": "claude-opus-4-6"}

    def test_to_agent_row_applies_single_field(self) -> None:
        # Arrange
        existing = make_row()
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        updated = patch.to_agent_row(existing)

        # Assert -- patched field changed
        assert updated.model == "claude-opus-4-6"
        # Assert -- all other fields unchanged
        assert updated.name == existing.name
        assert updated.backend == existing.backend
        assert updated.persona_json == existing.persona_json
        assert updated.voice_json == existing.voice_json
        assert updated.plugins_json == existing.plugins_json

    def test_to_agent_row_applies_multiple_fields(self) -> None:
        # Arrange
        existing = make_row()
        new_persona = '{"identity": {"display_name": "Aryl"}}'
        patch = RefinementPatch(
            fields={"model": "claude-opus-4-6", "persona_json": new_persona}
        )

        # Act
        updated = patch.to_agent_row(existing)

        # Assert
        assert updated.model == "claude-opus-4-6"
        assert updated.persona_json == new_persona
        assert updated.name == existing.name

    def test_to_agent_row_does_not_mutate_original(self) -> None:
        # Arrange
        existing = make_row()
        original_model = existing.model
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        patch.to_agent_row(existing)

        # Assert — original row is unchanged
        assert existing.model == original_model

    def test_as_json_multi_field_patch(self) -> None:
        # Arrange
        patch = RefinementPatch(fields={"model": "claude-opus-4-6", "streaming": True})

        # Act
        result = patch.as_json()

        # Assert
        parsed = json.loads(result)
        assert parsed["model"] == "claude-opus-4-6"
        assert parsed["streaming"] is True


# ---------------------------------------------------------------------------
# T5 — unknown agent raises ValueError (SC-9)
# ---------------------------------------------------------------------------


class TestRefineUnknownAgent:
    """AgentRefiner.read_profile() on an unknown agent raises ValueError."""

    def test_read_profile_unknown_agent_raises_value_error(self) -> None:
        # Arrange
        store = make_store(row=None)
        refiner = AgentRefiner("unknown", store)

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.read_profile()

    def test_apply_patch_unknown_agent_raises_value_error(self) -> None:
        # Arrange — store returns None on first get call
        store = make_store(row=None)
        refiner = AgentRefiner("unknown", store)
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.apply_patch(patch)


# ---------------------------------------------------------------------------
# T9 — apply_patch happy path (SC-1)
# ---------------------------------------------------------------------------


class TestApplyPatch:
    """AgentRefiner.apply_patch() — happy path and error branch."""

    def test_apply_patch_updates_and_returns_row(self) -> None:
        # Arrange
        row = make_row()
        store = make_store(row)
        refiner = AgentRefiner("lyra_default", store)
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act
        updated = refiner.apply_patch(patch)

        # Assert
        assert updated.model == "claude-opus-4-6"
        assert updated.name == "lyra_default"
        store.upsert.assert_awaited_once()

    def test_apply_patch_unknown_agent_raises_value_error(self) -> None:
        # Arrange
        store = make_store(row=None)
        refiner = AgentRefiner("missing", store)
        patch = RefinementPatch(fields={"model": "claude-opus-4-6"})

        # Act + Assert
        with pytest.raises(ValueError, match="not found"):
            refiner.apply_patch(patch)


# ---------------------------------------------------------------------------
# T12 — RefinementPatch allow-list validation (future: REFINABLE_FIELDS)
# ---------------------------------------------------------------------------


class TestRefinementPatchValidation:
    """RefinementPatch — allow-list validation via REFINABLE_FIELDS + __post_init__."""

    def test_disallowed_field_raises_value_error(self) -> None:
        # A security-sensitive field that must not be patchable via LLM
        with pytest.raises(ValueError, match="disallowed"):
            RefinementPatch(fields={"skip_permissions": True})

    def test_unknown_field_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="disallowed"):
            RefinementPatch(fields={"nonexistent_field": "value"})

    def test_valid_fields_accepted(self) -> None:
        # Should not raise -- model and persona_json are in REFINABLE_FIELDS
        patch = RefinementPatch(
            fields={
                "model": "claude-opus-4-6",
                "persona_json": '{"identity": {"display_name": "Lyra"}}',
            }
        )
        assert patch.fields["model"] == "claude-opus-4-6"
