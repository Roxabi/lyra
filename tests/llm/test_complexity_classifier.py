"""Tests for ComplexityClassifier (#134)."""

from __future__ import annotations

from lyra.core.agent.agent_config import Complexity
from lyra.llm.smart_routing import ComplexityClassifier


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
