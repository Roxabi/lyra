"""Tests for SmartRoutingDecorator and ComplexityClassifier (#134)."""

from __future__ import annotations

import tomllib
from unittest.mock import AsyncMock, MagicMock

import pytest

from lyra.core.agent_config import Complexity, ModelConfig, SmartRoutingConfig
from lyra.core.command_parser import CommandParser
from lyra.core.command_router import CommandRouter
from lyra.core.message import Attachment, InboundMessage
from lyra.core.plugin_loader import PluginLoader
from lyra.core.trust import TrustLevel
from lyra.llm.base import LlmResult
from lyra.llm.smart_routing import (
    ComplexityClassifier,
    ComplexityEstimator,  # type: ignore[attr-defined]  # not yet implemented (#153)
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
    inner.complete = (
        AsyncMock(return_value=make_ok_result())
        if return_values is None
        else MagicMock()
    )
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


_cmd_parser = CommandParser()


def _make_admin_msg(
    text: str = "/routing",
    user_id: str = "tg:user:123",
    *,
    is_admin: bool = True,
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
        is_admin=is_admin,
        command=_cmd_parser.parse(text),  # type: ignore[call-arg]  # field added in #153
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
        level, _ = self.classifier.classify("Explain the decorator pattern in Python")
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


# ---------------------------------------------------------------------------
# /routing admin command
# ---------------------------------------------------------------------------


class TestRoutingCommand:
    def _make_router(
        self,
        *,
        decorator: SmartRoutingDecorator | None = None,
    ) -> CommandRouter:
        loader = MagicMock(spec=PluginLoader)
        loader.get_commands.return_value = {}
        return CommandRouter(
            plugin_loader=loader,
            enabled_plugins=[],
            smart_routing_decorator=decorator,
        )

    async def test_routing_admin_only(self) -> None:
        """Non-admin users get rejected."""
        # Arrange
        router = self._make_router()
        msg = _make_admin_msg("/routing", is_admin=False)

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert "admin-only" in resp.content  # type: ignore[union-attr]

    async def test_routing_not_configured(self) -> None:
        """When no smart routing decorator, inform user."""
        # Arrange
        router = self._make_router(decorator=None)
        msg = _make_admin_msg("/routing")

        # Act
        resp = await router.dispatch(msg)

        # Assert
        assert "not configured" in resp.content  # type: ignore[union-attr]

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
        assert "No routing decisions" in resp.content  # type: ignore[union-attr]

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
        assert "trivial" in resp.content  # type: ignore[union-attr]
        assert "haiku" in resp.content  # type: ignore[union-attr]
        assert "1 decisions" in resp.content  # type: ignore[union-attr]


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


# ---------------------------------------------------------------------------
# T14 — ComplexityEstimator + SmartRoutingDecorator msg= param (#153)
# ---------------------------------------------------------------------------


def make_attachment() -> Attachment:
    """Create a minimal Attachment for testing."""
    return Attachment(
        type="image",
        url_or_path_or_bytes="http://example.com/img.png",
        mime_type="image/png",
        filename="test.png",
    )


class TestComplexityEstimator:
    """ComplexityEstimator signal table: text + attachments + command + turn count."""

    def _estimator(self, high_cmds: tuple[str, ...] = ()) -> ComplexityEstimator:
        return ComplexityEstimator(high_complexity_commands=high_cmds)

    def test_trivial_greeting_no_signals(self) -> None:
        # Arrange
        est = self._estimator()

        # Act
        complexity, reason = est.estimate("hi", [], None, 0)

        # Assert — short greeting with no extra signals → TRIVIAL
        assert complexity == Complexity.TRIVIAL
        assert "no signals" in reason

    def test_attachment_raises_complexity(self) -> None:
        # Arrange
        est = self._estimator()
        att = make_attachment()

        # Act
        complexity, reason = est.estimate("hi", [att], None, 0)

        # Assert — attachment adds score → at least SIMPLE
        assert complexity in (
            Complexity.SIMPLE,
            Complexity.MODERATE,
            Complexity.COMPLEX,
        )
        assert "attachment" in reason

    def test_high_complexity_command_raises(self) -> None:
        # Arrange
        est = self._estimator(high_cmds=("imagine",))

        # Act
        complexity, reason = est.estimate("hi", [], "imagine", 0)

        # Assert — command in HIGH_COMPLEXITY_COMMANDS adds +2 → MODERATE or above
        assert complexity in (Complexity.MODERATE, Complexity.COMPLEX)
        assert "command:imagine" in reason

    def test_unknown_command_no_bonus(self) -> None:
        # Arrange
        est = self._estimator(high_cmds=("imagine",))

        # Act
        complexity, _ = est.estimate("hi", [], "help", 0)

        # Assert — "help" is not in high_complexity_commands → no bonus → TRIVIAL
        assert complexity == Complexity.TRIVIAL

    def test_none_command_name_safe(self) -> None:
        # Arrange
        est = self._estimator(high_cmds=("imagine",))

        # Act — command_name=None must not raise
        complexity, _ = est.estimate("hi", [], None, 0)

        # Assert
        assert complexity == Complexity.TRIVIAL

    def test_high_turn_count_raises(self) -> None:
        # Arrange
        est = self._estimator()

        # Act — turn_count > 10 adds +1 to score
        complexity, reason = est.estimate("hi", [], None, 15)

        # Assert
        assert complexity != Complexity.TRIVIAL
        assert "turns" in reason

    def test_combined_signals_reach_complex(self) -> None:
        # Arrange — attachment (+1) + command:analyze (+2) + turns>10 (+1) = 4 → COMPLEX
        est = self._estimator(high_cmds=("analyze",))
        att = make_attachment()

        # Act
        complexity, reason = est.estimate("hi", [att], "analyze", 12)

        # Assert
        assert complexity == Complexity.COMPLEX


class TestSmartRoutingDecoratorWithMsg:
    """SmartRoutingDecorator.complete() backward compat and msg= param (#153 S2)."""

    @pytest.mark.asyncio
    async def test_msg_none_backward_compat(self) -> None:
        """msg=None → text-only classifier, same behavior as before."""
        # Arrange
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)

        # Act — msg=None is backward compat (#153 S2 not yet implemented)
        result = await decorator.complete(  # type: ignore[call-arg]
            "pool1",
            "hi",
            make_model_cfg(),
            "system",
            messages=None,
            msg=None,
        )

        # Assert — backward compat: returns normally
        assert result.result == "ok"

    @pytest.mark.asyncio
    async def test_msg_with_attachment_uses_estimator(self) -> None:
        """msg= with attachment → ComplexityEstimator used; reason contains signal."""
        # Arrange
        from datetime import datetime, timezone

        att = make_attachment()
        msg = InboundMessage(
            id="1",
            platform="telegram",
            bot_id="bot",
            scope_id="scope",
            user_id="user",
            user_name="Test",
            is_mention=False,
            text="hi",
            text_raw="hi",
            trust_level=TrustLevel.OWNER,
            attachments=[att],
            timestamp=datetime.now(timezone.utc),
        )
        inner = _make_inner()
        config = _make_config(enabled=True)
        decorator = SmartRoutingDecorator(inner, config)

        # Act — msg= param not yet implemented (#153 S2)
        await decorator.complete(  # type: ignore[call-arg]
            "pool1",
            "hi",
            make_model_cfg(),
            "system",
            messages=[],
            msg=msg,
        )

        # Assert — routing decision recorded with attachment signal in reason
        assert len(decorator.history) == 1
        decision = decorator.history[-1]
        assert "attachment" in decision.reason

    @pytest.mark.asyncio
    async def test_routing_decision_reason_contains_signals(self) -> None:
        """RoutingDecision.reason reflects multi-signal basis (SC-11 / SC-12)."""
        # Arrange
        from datetime import datetime, timezone

        from lyra.core.command_parser import CommandContext

        cmd = CommandContext(prefix="/", name="analyze", args="", raw="/analyze")
        msg = InboundMessage(
            id="2",
            platform="telegram",
            bot_id="bot",
            scope_id="scope",
            user_id="user",
            user_name="Test",
            is_mention=False,
            text="/analyze",
            text_raw="/analyze",
            trust_level=TrustLevel.OWNER,
            timestamp=datetime.now(timezone.utc),
            command=cmd,  # type: ignore[call-arg]  # field added in #153
        )
        inner = _make_inner()
        config = SmartRoutingConfig(
            enabled=True,
            routing_table={
                Complexity.TRIVIAL: "claude-haiku-4-5-20251001",
                Complexity.SIMPLE: "claude-haiku-4-5-20251001",
                Complexity.MODERATE: "claude-sonnet-4-6",
                Complexity.COMPLEX: "claude-opus-4-6",
            },
            history_size=50,
            high_complexity_commands=("analyze",),  # type: ignore[call-arg]  # field added in #153
        )
        decorator = SmartRoutingDecorator(inner, config)

        # Act — msg= param not yet implemented (#153 S2)
        await decorator.complete(  # type: ignore[call-arg]
            "pool1",
            "/analyze",
            make_model_cfg(),
            "system",
            messages=[],
            msg=msg,
        )

        # Assert — reason must contain the command signal that fired
        decision = decorator.history[-1]
        assert "command:analyze" in decision.reason

    @pytest.mark.asyncio
    async def test_bang_command_does_not_trigger_high_complexity(self) -> None:
        """!-prefixed commands must not match high_complexity_commands (SEC-1)."""
        # Arrange — "analyze" is in high_complexity_commands, but !analyze uses ! prefix
        from datetime import datetime, timezone

        from lyra.core.command_parser import CommandContext

        bang_cmd = CommandContext(prefix="!", name="analyze", args="", raw="!analyze")
        msg = InboundMessage(
            id="3",
            platform="telegram",
            bot_id="bot",
            scope_id="scope",
            user_id="user",
            user_name="Test",
            is_mention=False,
            text="!analyze",
            text_raw="!analyze",
            trust_level=TrustLevel.OWNER,
            timestamp=datetime.now(timezone.utc),
            command=bang_cmd,  # type: ignore[call-arg]
        )
        inner = _make_inner()
        config = SmartRoutingConfig(
            enabled=True,
            routing_table={
                Complexity.TRIVIAL: "claude-haiku-4-5-20251001",
                Complexity.SIMPLE: "claude-haiku-4-5-20251001",
                Complexity.MODERATE: "claude-sonnet-4-6",
                Complexity.COMPLEX: "claude-opus-4-6",
            },
            history_size=50,
            high_complexity_commands=("analyze",),  # type: ignore[call-arg]
        )
        decorator = SmartRoutingDecorator(inner, config)

        # Act
        await decorator.complete(  # type: ignore[call-arg]
            "pool1",
            "!analyze",
            make_model_cfg(),
            "system",
            messages=[],
            msg=msg,
        )

        # Assert — !analyze must NOT trigger the command:analyze signal
        decision = decorator.history[-1]
        assert "command:analyze" not in decision.reason
