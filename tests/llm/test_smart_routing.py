"""Tests for SmartRoutingDecorator and ComplexityClassifier (#134)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent import Complexity, ModelConfig, SmartRoutingConfig
from lyra.llm.base import LlmResult
from lyra.llm.smart_routing import ComplexityClassifier, SmartRoutingDecorator

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_model_cfg(model: str = "claude-sonnet-4-6") -> ModelConfig:
    return ModelConfig(backend="anthropic-sdk", model=model)


def make_ok_result(text: str = "ok") -> LlmResult:
    return LlmResult(result=text)


def _make_inner(return_values: list[LlmResult] | None = None) -> MagicMock:
    inner = MagicMock()
    inner.complete = AsyncMock(
        side_effect=return_values or [make_ok_result()],
    )
    inner.capabilities = {"streaming": False, "auth": "api_key"}
    return inner


def _make_config(
    enabled: bool = True,
    routing_table: dict[Complexity, str] | None = None,
    history_size: int = 50,
) -> SmartRoutingConfig:
    table = routing_table or {
        Complexity.TRIVIAL: "claude-haiku-4-5-20251001",
        Complexity.SIMPLE: "claude-haiku-4-5-20251001",
        Complexity.MODERATE: "claude-sonnet-4-6",
        Complexity.COMPLEX: "claude-opus-4-6",
    }
    return SmartRoutingConfig(
        enabled=enabled, routing_table=table, history_size=history_size
    )


# ---------------------------------------------------------------------------
# ComplexityClassifier
# ---------------------------------------------------------------------------


class TestComplexityClassifier:
    def setup_method(self) -> None:
        self.classifier = ComplexityClassifier()

    def test_trivial_greeting(self) -> None:
        level, _ = self.classifier.classify("hello")
        assert level == Complexity.TRIVIAL

    def test_trivial_yes_no(self) -> None:
        level, _ = self.classifier.classify("yes")
        assert level == Complexity.TRIVIAL

    def test_trivial_short(self) -> None:
        level, _ = self.classifier.classify("ok")
        assert level == Complexity.TRIVIAL

    def test_simple_factual(self) -> None:
        level, _ = self.classifier.classify("What time is it in Paris?")
        assert level == Complexity.SIMPLE

    def test_moderate_explain(self) -> None:
        level, _ = self.classifier.classify(
            "Explain the decorator pattern in Python"
        )
        assert level == Complexity.MODERATE

    def test_moderate_summarize(self) -> None:
        level, _ = self.classifier.classify(
            "Summarize the key points of this article for me"
        )
        assert level == Complexity.MODERATE

    def test_complex_code_keywords(self) -> None:
        level, _ = self.classifier.classify(
            "Write a Python function that validates email addresses"
        )
        assert level == Complexity.COMPLEX

    def test_complex_analyze(self) -> None:
        level, _ = self.classifier.classify(
            "Analyze the performance bottleneck in our database queries"
        )
        assert level == Complexity.COMPLEX

    def test_complex_long_text(self) -> None:
        long_text = " ".join(["word"] * 150)
        level, _ = self.classifier.classify(long_text)
        assert level == Complexity.COMPLEX

    def test_returns_reason_string(self) -> None:
        _, reason = self.classifier.classify("hello")
        assert isinstance(reason, str)
        assert len(reason) > 0


# ---------------------------------------------------------------------------
# SmartRoutingDecorator
# ---------------------------------------------------------------------------


class TestSmartRoutingDecorator:
    async def test_disabled_passthrough(self) -> None:
        """When disabled, inner is called with original model_cfg unchanged."""
        inner = _make_inner()
        config = _make_config(enabled=False)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        result = await decorator.complete("p1", "hello", model_cfg, "sys")

        assert result.ok
        # Inner called with original model_cfg
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-sonnet-4-6"

    async def test_routes_trivial_to_haiku(self) -> None:
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        await decorator.complete("p1", "hi", model_cfg, "sys")

        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-haiku-4-5-20251001"

    async def test_routes_complex_to_opus(self) -> None:
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-haiku-4-5-20251001")

        await decorator.complete(
            "p1",
            "Analyze the performance of our caching layer in detail",
            model_cfg,
            "sys",
        )

        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-opus-4-6"

    async def test_model_cfg_not_mutated(self) -> None:
        """Original model_cfg must not be mutated by the decorator."""
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        await decorator.complete("p1", "hi", model_cfg, "sys")

        # Original still has sonnet
        assert model_cfg.model == "claude-sonnet-4-6"

    async def test_classifier_exception_fallback(self) -> None:
        """If classifier raises, fall back to original model."""
        inner = _make_inner()
        config = _make_config(enabled=True)
        bad_classifier = MagicMock()
        bad_classifier.classify.side_effect = RuntimeError("boom")
        decorator = SmartRoutingDecorator(inner, config, classifier=bad_classifier)
        model_cfg = make_model_cfg("claude-sonnet-4-6")

        result = await decorator.complete("p1", "hello", model_cfg, "sys")

        assert result.ok
        call_args = inner.complete.call_args
        assert call_args[0][2].model == "claude-sonnet-4-6"

    async def test_history_stored(self) -> None:
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)

        await decorator.complete("p1", "hello", make_model_cfg(), "sys")

        assert len(decorator.history) == 1
        decision = decorator.history[0]
        assert decision.complexity == Complexity.TRIVIAL
        assert decision.routed_model == "claude-haiku-4-5-20251001"

    async def test_history_capped(self) -> None:
        inner = _make_inner([make_ok_result()] * 5)
        config = _make_config(enabled=True, history_size=2)
        decorator = SmartRoutingDecorator(inner, config)

        for _ in range(5):
            await decorator.complete("p1", "hi", make_model_cfg(), "sys")

        assert len(decorator.history) == 2

    async def test_capabilities_forwarded(self) -> None:
        inner = _make_inner()
        config = _make_config(enabled=False)
        decorator = SmartRoutingDecorator(inner, config)

        assert decorator.capabilities == inner.capabilities
