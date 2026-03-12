"""Tests for SmartRoutingDecorator and ComplexityClassifier (#134)."""

from __future__ import annotations

import tomllib
from unittest.mock import AsyncMock, MagicMock

from lyra.core.agent import Complexity, ModelConfig, SmartRoutingConfig
from lyra.core.command_router import CommandRouter
from lyra.core.message import InboundMessage
from lyra.core.plugin_loader import PluginLoader
from lyra.core.trust import TrustLevel
from lyra.llm.base import LlmResult
from lyra.llm.smart_routing import (
    ComplexityClassifier,
    SmartRoutingDecorator,
)

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
        return_value=make_ok_result()
    ) if return_values is None else MagicMock()
    if return_values is not None:
        inner.complete = AsyncMock(side_effect=return_values)
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


def _make_admin_msg(
    text: str = "/routing", user_id: str = "tg:user:123"
) -> InboundMessage:
    return InboundMessage(
        id="msg1",
        platform="telegram",
        bot_id="bot1",
        scope_id="scope1",
        user_id=user_id,
        user_name="admin",
        is_mention=False,
        text=text,
        text_raw=text,
        trust_level=TrustLevel.TRUSTED,
    )


# ---------------------------------------------------------------------------
# ComplexityClassifier
# ---------------------------------------------------------------------------


class TestComplexityClassifier:
    def setup_method(self) -> None:
        self.classifier = ComplexityClassifier()

    def test_trivial_greeting(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify("hello")
        # Assert
        assert level == Complexity.TRIVIAL

    def test_trivial_yes_no(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify("yes")
        # Assert
        assert level == Complexity.TRIVIAL

    def test_trivial_short(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify("ok")
        # Assert
        assert level == Complexity.TRIVIAL

    def test_simple_factual(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify("What time is it in Paris?")
        # Assert
        assert level == Complexity.SIMPLE

    def test_moderate_explain(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify(
            "Explain the decorator pattern in Python"
        )
        # Assert
        assert level == Complexity.MODERATE

    def test_moderate_summarize(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify(
            "Summarize the key points of this article for me"
        )
        # Assert
        assert level == Complexity.MODERATE

    def test_complex_code_keywords(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify(
            "Write a Python function that validates email addresses"
        )
        # Assert
        assert level == Complexity.COMPLEX

    def test_complex_analyze(self) -> None:
        # Arrange / Act
        level, _ = self.classifier.classify(
            "Analyze the performance bottleneck in our database queries"
        )
        # Assert
        assert level == Complexity.COMPLEX

    def test_complex_long_text(self) -> None:
        # Arrange
        long_text = " ".join(["word"] * 150)
        # Act
        level, _ = self.classifier.classify(long_text)
        # Assert
        assert level == Complexity.COMPLEX

    def test_returns_reason_string(self) -> None:
        # Arrange / Act
        _, reason = self.classifier.classify("hello")
        # Assert
        assert isinstance(reason, str)
        assert len(reason) > 0


# ---------------------------------------------------------------------------
# SmartRoutingDecorator
# ---------------------------------------------------------------------------


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
        await decorator.complete(
            "p1", "What time is it in Paris?", model_cfg, "sys"
        )

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


# ---------------------------------------------------------------------------
# /routing admin command
# ---------------------------------------------------------------------------


class TestRoutingCommand:
    def _make_router(
        self,
        *,
        admin_ids: set[str] | None = None,
        decorator: SmartRoutingDecorator | None = None,
    ) -> CommandRouter:
        loader = MagicMock(spec=PluginLoader)
        loader.get_commands.return_value = {}
        return CommandRouter(
            plugin_loader=loader,
            enabled_plugins=[],
            admin_user_ids=admin_ids or {"tg:user:123"},
            smart_routing_decorator=decorator,
        )

    async def test_routing_admin_only(self) -> None:
        """Non-admin users get rejected."""
        # Arrange
        router = self._make_router(admin_ids={"tg:user:999"})
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert "admin-only" in resp.content

    async def test_routing_not_configured(self) -> None:
        """When no smart routing decorator, inform user."""
        # Arrange
        router = self._make_router(decorator=None)
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert "not configured" in resp.content

    async def test_routing_empty_history(self) -> None:
        """When history is empty, show appropriate message."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        dec = SmartRoutingDecorator(inner, config)
        router = self._make_router(decorator=dec)
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert "No routing decisions" in resp.content

    async def test_routing_shows_history(self) -> None:
        """After routing, /routing shows decisions."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        dec = SmartRoutingDecorator(inner, config)
        await dec.complete("p1", "hello", make_model_cfg(), "sys")
        router = self._make_router(decorator=dec)
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert "trivial" in resp.content
        assert "haiku" in resp.content
        assert "1 decisions" in resp.content


# ---------------------------------------------------------------------------
# TOML config loading
# ---------------------------------------------------------------------------


class TestSmartRoutingTomlConfig:
    def test_routing_table_parsed_from_toml(self) -> None:
        """All four complexity levels are parsed from TOML."""
        # Arrange
        toml_str = b"""
[agent]
name = "test"
memory_namespace = "test"

[model]
backend = "anthropic-sdk"
model = "claude-sonnet-4-6"

[agent.smart_routing]
enabled = true

[agent.smart_routing.models]
trivial  = "claude-haiku-4-5-20251001"
simple   = "claude-haiku-4-5-20251001"
moderate = "claude-sonnet-4-6"
complex  = "claude-opus-4-6"
"""
        data = tomllib.loads(toml_str.decode())

        # Act — replicate the parsing logic from load_agent_config
        agent_section = data["agent"]
        sr_section = agent_section.get("smart_routing")
        assert sr_section is not None
        sr_models = sr_section.get("models", {})
        routing_table: dict[Complexity, str] = {}
        for level in Complexity:
            model_id = sr_models.get(level.value)
            if model_id:
                routing_table[level] = model_id
        config = SmartRoutingConfig(
            enabled=bool(sr_section.get("enabled", False)),
            routing_table=routing_table,
        )

        # Assert
        assert config.enabled is True
        assert len(config.routing_table) == 4
        assert config.routing_table[Complexity.TRIVIAL] == "claude-haiku-4-5-20251001"
        assert config.routing_table[Complexity.SIMPLE] == "claude-haiku-4-5-20251001"
        assert config.routing_table[Complexity.MODERATE] == "claude-sonnet-4-6"
        assert config.routing_table[Complexity.COMPLEX] == "claude-opus-4-6"
