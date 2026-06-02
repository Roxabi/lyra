"""Backward-compatibility shim — LlmProvider and LlmResult now live in core/ports/.

All new code should import from factory.core.ports.llm directly.
This module re-exports both names so existing imports continue to work.
"""

from factory.core.ports.llm import LlmProvider, LlmResult

__all__ = ["LlmProvider", "LlmResult"]
