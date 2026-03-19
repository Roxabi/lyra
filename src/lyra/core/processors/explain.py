"""ExplainProcessor — /explain <url> (issue #363).

Pre:  validate URL → scrape → inject content + plain-language instruction.
Post: pass-through (response goes straight to history).
"""

from __future__ import annotations

from lyra.core.processor_registry import register
from lyra.core.processors._scraping import ScrapingProcessor

_INSTRUCTION = (
    "Please explain the following web content in plain language "
    "suitable for sharing in a chat message. Be concise and focus on key points."
)


@register(
    "/explain",
    description="Explain a URL in plain language: /explain <url>",
)
class ExplainProcessor(ScrapingProcessor):
    """Scrape URL → inject content → LLM explains in plain language."""

    @property
    def instruction(self) -> str:
        return _INSTRUCTION
