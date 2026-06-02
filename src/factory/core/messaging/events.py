"""LLM streaming event types.

These frozen dataclasses represent the raw events emitted by LLM drivers during
streaming. They form the input side of the LLM → StreamProcessor → RenderEvent
pipeline.

They live in ``core/`` rather than ``llm/`` because the type system is a
domain-level contract consumed by both sides of the pipeline — keeping it here
lets ``llm/`` depend on ``core/`` unidirectionally (enforced by ``import-linter``).

No framework imports (aiogram, discord, anthropic) are permitted in this module.

Canonical definitions have been moved to ``core/ports/llm_types.py`` so that
the driven port (``core/ports/llm.py``) is fully self-contained (#1661).
This module re-exports all names for backward compatibility.
"""

from __future__ import annotations

# Re-export all LlmEvent variants from the canonical port-types module.
# All internal callers continue to import from here without change.
from ..ports.llm_types import (
    LlmEvent as LlmEvent,
)
from ..ports.llm_types import (
    ResultLlmEvent as ResultLlmEvent,
)
from ..ports.llm_types import (
    TextLlmEvent as TextLlmEvent,
)
from ..ports.llm_types import (
    ThinkingLlmEvent as ThinkingLlmEvent,
)
from ..ports.llm_types import (
    ToolResultLlmEvent as ToolResultLlmEvent,
)
from ..ports.llm_types import (
    ToolUseDeltaLlmEvent as ToolUseDeltaLlmEvent,
)
from ..ports.llm_types import (
    ToolUseEndLlmEvent as ToolUseEndLlmEvent,
)
from ..ports.llm_types import (
    ToolUseLlmEvent as ToolUseLlmEvent,
)

__all__ = [
    "LlmEvent",
    "ResultLlmEvent",
    "TextLlmEvent",
    "ThinkingLlmEvent",
    "ToolResultLlmEvent",
    "ToolUseDeltaLlmEvent",
    "ToolUseEndLlmEvent",
    "ToolUseLlmEvent",
]
