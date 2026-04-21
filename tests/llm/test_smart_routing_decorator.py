"""Tests for SmartRoutingDecorator (#134)."""

from __future__ import annotations

from unittest.mock import MagicMock

from lyra.core.agent.agent_config import Complexity
from lyra.llm.smart_routing import SmartRoutingDecorator

from .conftest import _make_config, _make_inner, make_model_cfg


class TestSmartRoutingDecorator:
    async def test_disabled_passthrough(self) -> None:
        """When disabled, inner is called with original model_cfg unchanged."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=False)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        # Act
        result = await decorator.complete("p1", "hello", model_cfg, "sys")

        # Assert
        assert result.ok
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-sonnet-4-6"

    async def test_routes_trivial_to_haiku(self) -> None:
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        # Act
        await decorator.complete("p1", "hi", model_cfg, "sys")

        # Assert
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-haiku-4-5-20251001"

    async def test_routes_simple_to_haiku(self) -> None:
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        # Act
        await decorator.complete("p1", "What time is it in Paris?", model_cfg, "sys")

        # Assert
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-haiku-4-5-20251001"

    async def test_routes_moderate_to_sonnet(self) -> None:
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-haiku-4-5-20251001")

        # Act
        await decorator.complete(
            "p1",
            "Explain the decorator pattern in Python",
            model_cfg,
            "sys",
        )

        # Assert
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-sonnet-4-6"

    async def test_routes_complex_to_opus(self) -> None:
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-haiku-4-5-20251001")

        # Act
        await decorator.complete(
            "p1",
            "Analyze the performance of our caching layer in detail",
            model_cfg,
            "sys",
        )

        # Assert
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-opus-4-6"

    async def test_model_cfg_not_mutated(self) -> None:
        """Original model_cfg must not be mutated by the decorator."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        # Act
        await decorator.complete("p1", "hi", model_cfg, "sys")

        # Assert — original still has sonnet
        assert model_cfg.model == "claude-sonnet-4-6"

    async def test_classifier_exception_fallback(self) -> None:
        """If classifier raises, fall back to original model."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        bad_classifier = MagicMock()
        bad_classifier.classify.side_effect = RuntimeError("boom")
        decorator = SmartRoutingDecorator(inner, config, classifier=bad_classifier)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        # Act
        result = await decorator.complete("p1", "hello", model_cfg, "sys")

        # Assert
        assert result.ok
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-sonnet-4-6"

    async def test_history_stored_all_fields(self) -> None:
        """All RoutingDecision fields are populated correctly."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)

        # Act
        await decorator.complete(
            "p1", "hello", make_model_cfg("claude-sonnet-4-6"), "sys"
        )

        # Assert
        assert len(decorator.history) == 1
        d = decorator.history[0]
        assert d.complexity == Complexity.TRIVIAL
        assert d.original_model == "claude-sonnet-4-6"
        assert d.routed_model == "claude-haiku-4-5-20251001"
        assert isinstance(d.reason, str) and len(d.reason) > 0
        assert isinstance(d.timestamp, float) and d.timestamp > 0
        assert d.message_preview == "hello"

    async def test_history_capped(self) -> None:
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True, history_size=2)
        decorator = SmartRoutingDecorator(inner, config)

        # Act
        for _ in range(5):
            await decorator.complete("p1", "hi", make_model_cfg(), "sys")

        # Assert
        assert len(decorator.history) == 2

    async def test_capabilities_forwarded(self) -> None:
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=False)

        # Act
        decorator = SmartRoutingDecorator(inner, config)

        # Assert
        assert decorator.capabilities == inner.capabilities
