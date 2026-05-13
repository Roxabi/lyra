"""Tests for v2 Text dataclasses in lyra.core.messaging.render_events (T3 / #1099).

Covers: TextStartRenderEvent, TextDeltaRenderEvent, TextEndRenderEvent,
TextChunkRenderEvent — instantiation, frozen contract, schema_version default.

Source: src/lyra/core/messaging/render_events.py
"""

from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from lyra.core.messaging.render_events import (
    SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_END_RENDER_EVENT,
    SCHEMA_VERSION_TEXT_START_RENDER_EVENT,
    TextChunkRenderEvent,
    TextDeltaRenderEvent,
    TextEndRenderEvent,
    TextStartRenderEvent,
)

# ---------------------------------------------------------------------------
# TextStartRenderEvent
# ---------------------------------------------------------------------------


class TestTextStartRenderEvent:
    def test_instantiation_all_fields(self) -> None:
        # Arrange / Act
        e = TextStartRenderEvent(message_id="text-abc123")
        # Assert
        assert e.message_id == "text-abc123"
        assert e.role == "assistant"
        assert e.schema_version == SCHEMA_VERSION_TEXT_START_RENDER_EVENT

    def test_role_default_is_assistant(self) -> None:
        e = TextStartRenderEvent(message_id="m1")
        assert e.role == "assistant"

    def test_schema_version_constant_equals_one(self) -> None:
        assert SCHEMA_VERSION_TEXT_START_RENDER_EVENT == 1

    def test_schema_version_default_equals_constant(self) -> None:
        e = TextStartRenderEvent(message_id="m1")
        assert e.schema_version == SCHEMA_VERSION_TEXT_START_RENDER_EVENT

    def test_frozen_message_id(self) -> None:
        e = TextStartRenderEvent(message_id="m1")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.message_id = "x"  # type: ignore[misc]

    def test_frozen_role(self) -> None:
        e = TextStartRenderEvent(message_id="m1")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.role = "user"  # type: ignore[misc]

    def test_frozen_schema_version(self) -> None:
        e = TextStartRenderEvent(message_id="m1")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.schema_version = 99  # type: ignore[misc]

    def test_equality(self) -> None:
        a = TextStartRenderEvent(message_id="m1")
        b = TextStartRenderEvent(message_id="m1")
        assert a == b
        assert a != TextStartRenderEvent(message_id="m2")


# ---------------------------------------------------------------------------
# TextDeltaRenderEvent
# ---------------------------------------------------------------------------


class TestTextDeltaRenderEvent:
    def test_instantiation_all_fields(self) -> None:
        e = TextDeltaRenderEvent(message_id="text-abc123", delta="Hello")
        assert e.message_id == "text-abc123"
        assert e.delta == "Hello"
        assert e.schema_version == SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT

    def test_schema_version_constant_equals_one(self) -> None:
        assert SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT == 1

    def test_schema_version_default_equals_constant(self) -> None:
        e = TextDeltaRenderEvent(message_id="m1", delta="chunk")
        assert e.schema_version == SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT

    def test_frozen_message_id(self) -> None:
        e = TextDeltaRenderEvent(message_id="m1", delta="chunk")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.message_id = "x"  # type: ignore[misc]

    def test_frozen_delta(self) -> None:
        e = TextDeltaRenderEvent(message_id="m1", delta="chunk")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.delta = "y"  # type: ignore[misc]

    def test_frozen_schema_version(self) -> None:
        e = TextDeltaRenderEvent(message_id="m1", delta="chunk")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.schema_version = 99  # type: ignore[misc]

    def test_delta_empty_string_is_valid(self) -> None:
        """Empty delta is a valid streaming chunk (end of whitespace token)."""
        e = TextDeltaRenderEvent(message_id="m1", delta="")
        assert e.delta == ""

    def test_equality(self) -> None:
        a = TextDeltaRenderEvent(message_id="m1", delta="hi")
        b = TextDeltaRenderEvent(message_id="m1", delta="hi")
        assert a == b
        assert a != TextDeltaRenderEvent(message_id="m1", delta="bye")


# ---------------------------------------------------------------------------
# TextEndRenderEvent
# ---------------------------------------------------------------------------


class TestTextEndRenderEvent:
    def test_instantiation_all_fields(self) -> None:
        e = TextEndRenderEvent(message_id="text-abc123")
        assert e.message_id == "text-abc123"
        assert e.schema_version == SCHEMA_VERSION_TEXT_END_RENDER_EVENT

    def test_schema_version_constant_equals_one(self) -> None:
        assert SCHEMA_VERSION_TEXT_END_RENDER_EVENT == 1

    def test_schema_version_default_equals_constant(self) -> None:
        e = TextEndRenderEvent(message_id="m1")
        assert e.schema_version == SCHEMA_VERSION_TEXT_END_RENDER_EVENT

    def test_frozen_message_id(self) -> None:
        e = TextEndRenderEvent(message_id="m1")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.message_id = "x"  # type: ignore[misc]

    def test_frozen_schema_version(self) -> None:
        e = TextEndRenderEvent(message_id="m1")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.schema_version = 99  # type: ignore[misc]

    def test_equality(self) -> None:
        a = TextEndRenderEvent(message_id="m1")
        b = TextEndRenderEvent(message_id="m1")
        assert a == b
        assert a != TextEndRenderEvent(message_id="m2")


# ---------------------------------------------------------------------------
# TextChunkRenderEvent
# ---------------------------------------------------------------------------


class TestTextChunkRenderEvent:
    def test_instantiation_all_fields(self) -> None:
        e = TextChunkRenderEvent(message_id="text-abc123", delta="Hello")
        assert e.message_id == "text-abc123"
        assert e.delta == "Hello"
        assert e.role == "assistant"
        assert e.schema_version == SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT

    def test_role_default_is_assistant(self) -> None:
        e = TextChunkRenderEvent(message_id="m1", delta="chunk")
        assert e.role == "assistant"

    def test_schema_version_constant_equals_one(self) -> None:
        assert SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT == 1

    def test_schema_version_default_equals_constant(self) -> None:
        e = TextChunkRenderEvent(message_id="m1", delta="chunk")
        assert e.schema_version == SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT

    def test_frozen_message_id(self) -> None:
        e = TextChunkRenderEvent(message_id="m1", delta="chunk")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.message_id = "x"  # type: ignore[misc]

    def test_frozen_delta(self) -> None:
        e = TextChunkRenderEvent(message_id="m1", delta="chunk")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.delta = "y"  # type: ignore[misc]

    def test_frozen_role(self) -> None:
        e = TextChunkRenderEvent(message_id="m1", delta="chunk")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.role = "user"  # type: ignore[misc]

    def test_frozen_schema_version(self) -> None:
        e = TextChunkRenderEvent(message_id="m1", delta="chunk")
        with pytest.raises((FrozenInstanceError, AttributeError)):
            e.schema_version = 99  # type: ignore[misc]

    def test_equality(self) -> None:
        a = TextChunkRenderEvent(message_id="m1", delta="hi")
        b = TextChunkRenderEvent(message_id="m1", delta="hi")
        assert a == b
        assert a != TextChunkRenderEvent(message_id="m1", delta="bye")


# ---------------------------------------------------------------------------
# Negative-test: guard deletion simulation
# Each guard below must fail when the class/constant is removed.
# ---------------------------------------------------------------------------


class TestNegativeGuards:
    def test_start_is_frozen_not_just_tautological(self) -> None:
        """frozen=True is enforced: assignment must raise, not silently succeed."""
        e = TextStartRenderEvent(message_id="guard-test")
        raised = False
        try:
            e.message_id = "mutated"  # type: ignore[misc]
        except (FrozenInstanceError, AttributeError):
            raised = True
        assert raised, "TextStartRenderEvent must reject field mutation"

    def test_delta_is_frozen_not_just_tautological(self) -> None:
        e = TextDeltaRenderEvent(message_id="guard-test", delta="d")
        raised = False
        try:
            e.delta = "mutated"  # type: ignore[misc]
        except (FrozenInstanceError, AttributeError):
            raised = True
        assert raised, "TextDeltaRenderEvent must reject field mutation"

    def test_end_is_frozen_not_just_tautological(self) -> None:
        e = TextEndRenderEvent(message_id="guard-test")
        raised = False
        try:
            e.message_id = "mutated"  # type: ignore[misc]
        except (FrozenInstanceError, AttributeError):
            raised = True
        assert raised, "TextEndRenderEvent must reject field mutation"

    def test_chunk_is_frozen_not_just_tautological(self) -> None:
        e = TextChunkRenderEvent(message_id="guard-test", delta="d")
        raised = False
        try:
            e.delta = "mutated"  # type: ignore[misc]
        except (FrozenInstanceError, AttributeError):
            raised = True
        assert raised, "TextChunkRenderEvent must reject field mutation"

    def test_schema_constants_are_imported_from_module(self) -> None:
        """If T2 removes a constant, this import itself fails."""
        import lyra.core.messaging.render_events as m

        assert hasattr(m, "SCHEMA_VERSION_TEXT_START_RENDER_EVENT")
        assert hasattr(m, "SCHEMA_VERSION_TEXT_DELTA_RENDER_EVENT")
        assert hasattr(m, "SCHEMA_VERSION_TEXT_END_RENDER_EVENT")
        assert hasattr(m, "SCHEMA_VERSION_TEXT_CHUNK_RENDER_EVENT")
