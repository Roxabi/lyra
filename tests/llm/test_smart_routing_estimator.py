"""Tests for ComplexityEstimator and SmartRoutingDecorator msg= param (#153)."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from lyra.core.agent_config import Complexity, SmartRoutingConfig
from lyra.core.commands.command_parser import CommandContext
from lyra.core.message import Attachment, InboundMessage
from lyra.core.trust import TrustLevel
from lyra.llm.smart_routing import (
    ComplexityEstimator,  # type: ignore[attr-defined]  # not yet implemented (#153)
    SmartRoutingDecorator,
)

from .conftest import _make_config, _make_inner, make_model_cfg

# ---------------------------------------------------------------------------
# File-local helper
# ---------------------------------------------------------------------------


def make_attachment() -> Attachment:
    """Create a minimal Attachment for testing."""
    return Attachment(
        type="image",
        url_or_path_or_bytes="http://example.com/img.png",
        mime_type="image/png",
        filename="test.png",
    )


# ---------------------------------------------------------------------------
# T14 — ComplexityEstimator + SmartRoutingDecorator msg= param (#153)
# ---------------------------------------------------------------------------


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
        complexity, _ = est.estimate("hi", [att], "analyze", 12)

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
