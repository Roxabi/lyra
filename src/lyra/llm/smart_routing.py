"""LLM smart routing: complexity-based model selection (#134).

Classifies message complexity via heuristics and routes to the cheapest
model capable of handling it. Wraps any LlmProvider as a decorator.

Stack position: CircuitBreaker → SmartRouting → Retry → Driver
"""

from __future__ import annotations

import dataclasses
import logging
import re
import time
from collections import deque
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from lyra.core.agent import Complexity, ModelConfig, SmartRoutingConfig
from lyra.llm.base import LlmProvider, LlmResult

if TYPE_CHECKING:
    from lyra.core.message import InboundMessage

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class RoutingDecision:
    """Record of a single routing decision."""

    complexity: Complexity
    original_model: str
    routed_model: str
    reason: str
    timestamp: float
    message_preview: str


# ---------------------------------------------------------------------------
# Heuristic classifier
# ---------------------------------------------------------------------------

_GREETING_PATTERNS = re.compile(
    r"^(h(i|ello|ey|owdy)|yo|sup|bonjour|salut|coucou|bye|thanks?|ty|ok|okay|sure|yep|nope|yes|no|gm|gn)\b",
    re.IGNORECASE,
)
_COMPLEX_KEYWORDS = re.compile(
    r"\b(analyze|analyse|compare|design|implement|architect|refactor|debug|optimize|review|write\s+(?:\w+\s+){0,6}(?:code|function|class|script|program|test))\b",
    re.IGNORECASE,
)
_MODERATE_KEYWORDS = re.compile(
    r"\b(explain|summarize|summarise|describe|elaborate|how\s+does|why\s+does|what\s+is\s+the\s+difference|walk\s+me\s+through)\b",
    re.IGNORECASE,
)


class ComplexityClassifier:
    """Zero-cost heuristic classifier for message complexity."""

    def classify(self, text: str) -> tuple[Complexity, str]:
        """Classify text complexity using heuristics.

        Returns (Complexity, reason_string).
        """
        stripped = text.strip()
        words = stripped.split()
        word_count = len(words)

        # Trivial: very short messages, greetings, yes/no
        if word_count <= 3 and _GREETING_PATTERNS.match(stripped):
            return Complexity.TRIVIAL, f"greeting/short ({word_count} words)"

        if word_count <= 2:
            return Complexity.TRIVIAL, f"very short ({word_count} words)"

        # Complex: explicit code/analysis keywords
        if _COMPLEX_KEYWORDS.search(stripped):
            return Complexity.COMPLEX, "complex keywords detected"

        # Complex: long messages (>100 words)
        if word_count > 100:
            return Complexity.COMPLEX, f"long message ({word_count} words)"

        # Moderate: explanation/reasoning keywords
        if _MODERATE_KEYWORDS.search(stripped):
            return Complexity.MODERATE, "reasoning/explanation keywords"

        # Moderate: medium-length messages (20-100 words)
        if word_count > 20:
            return Complexity.MODERATE, f"medium length ({word_count} words)"

        # Simple: everything else (3-20 words, no special keywords)
        return Complexity.SIMPLE, f"short factual ({word_count} words)"


# ---------------------------------------------------------------------------
# Multi-signal ComplexityEstimator
# ---------------------------------------------------------------------------


class ComplexityEstimator:
    """Multi-signal complexity estimator.

    Signals (additive score):
      COMPLEX text heuristic → +2
      MODERATE text heuristic → +1
      len(attachments) > 0 → +1
      command_name in high_complexity_commands → +2
      turn_count > 20 → +2  (stacks: counts as both >10 and >20)
      turn_count > 10 → +1

    Score → Complexity: 0=TRIVIAL, 1=SIMPLE, 2-3=MODERATE, 4+=COMPLEX
    """

    def __init__(
        self,
        text_classifier: ComplexityClassifier | None = None,
        high_complexity_commands: tuple[str, ...] = (),
    ) -> None:
        self._text_classifier = text_classifier or ComplexityClassifier()
        self._high_complexity_commands = high_complexity_commands

    def estimate(
        self,
        text: str,
        attachments: list,
        command_name: str | None,
        turn_count: int,
    ) -> tuple[Complexity, str]:
        text_complexity, text_reason = self._text_classifier.classify(text)
        score = 0
        signals: list[str] = []

        if text_complexity == Complexity.COMPLEX:
            score += 2
            signals.append(f"text:{text_reason}")
        elif text_complexity == Complexity.MODERATE:
            score += 1
            signals.append(f"text:{text_reason}")

        if attachments:
            score += 1
            signals.append("attachment +1")

        if command_name is not None and command_name in self._high_complexity_commands:
            score += 2
            signals.append(f"command:{command_name} +2")

        if turn_count > 20:
            score += 2
            signals.append(f"turns:{turn_count} +2")
        elif turn_count > 10:
            score += 1
            signals.append(f"turns:{turn_count} +1")

        reason = ", ".join(signals) if signals else "no signals"
        if score == 0:
            return Complexity.TRIVIAL, reason
        if score == 1:
            return Complexity.SIMPLE, reason
        if score <= 3:
            return Complexity.MODERATE, reason
        return Complexity.COMPLEX, reason


# ---------------------------------------------------------------------------
# SmartRoutingDecorator
# ---------------------------------------------------------------------------


class SmartRoutingDecorator:
    """Routes messages to different models based on complexity.

    When enabled, classifies each message and overrides model_cfg.model
    with the target from the routing table. When disabled, passes through
    unchanged with zero overhead.
    """

    def __init__(
        self,
        inner: LlmProvider,
        config: SmartRoutingConfig,
        classifier: ComplexityClassifier | None = None,
    ) -> None:
        self._inner = inner
        self._config = config
        self._classifier = classifier or ComplexityClassifier()
        self._estimator = ComplexityEstimator(
            text_classifier=self._classifier,
            high_complexity_commands=getattr(config, "high_complexity_commands", ()),
        )
        self._history: deque[RoutingDecision] = deque(maxlen=config.history_size)
        self.capabilities: dict[str, Any] = inner.capabilities

    @property
    def history(self) -> deque[RoutingDecision]:
        """Read-only access to routing decision history."""
        return self._history

    async def complete(  # noqa: PLR0913
        self,
        pool_id: str,
        text: str,
        model_cfg: ModelConfig,
        system_prompt: str,
        *,
        messages: list[dict] | None = None,
        on_intermediate: Callable[[str], Awaitable[None]] | None = None,
        msg: InboundMessage | None = None,
    ) -> LlmResult:
        if not self._config.enabled:
            return await self._inner.complete(
                pool_id,
                text,
                model_cfg,
                system_prompt,
                messages=messages,
                on_intermediate=on_intermediate,
            )

        # Classify and route
        original_model = model_cfg.model
        routed_cfg = model_cfg
        complexity = Complexity.SIMPLE
        reason = "default"

        try:
            if msg is not None:
                turn_count = len(messages) if messages else 0
                attachments = list(msg.attachments)
                command_name = msg.command.name if msg.command else None
                complexity, reason = self._estimator.estimate(
                    text, attachments, command_name, turn_count
                )
            else:
                # backward compat: text-only classification
                complexity, reason = self._classifier.classify(text)

            target_model = self._config.routing_table.get(complexity, original_model)
            if target_model != original_model:
                routed_cfg = dataclasses.replace(model_cfg, model=target_model)
        except Exception:
            log.warning(
                "Smart routing classifier failed, falling back to default model",
                exc_info=True,
            )
            routed_cfg = model_cfg
            reason = "classifier_error (fallback)"

        result = await self._inner.complete(
            pool_id,
            text,
            routed_cfg,
            system_prompt,
            messages=messages,
            on_intermediate=on_intermediate,
        )

        # Record decision
        preview = text[:40] + ("..." if len(text) > 40 else "")
        self._history.append(
            RoutingDecision(
                complexity=complexity,
                original_model=original_model,
                routed_model=routed_cfg.model,
                reason=reason,
                timestamp=time.time(),
                message_preview=preview,
            )
        )

        return result

    def is_alive(self, pool_id: str) -> bool:
        return self._inner.is_alive(pool_id)
