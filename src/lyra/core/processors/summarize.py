"""SummarizeProcessor — /summarize <url> (issue #363).

Pre:  validate URL → scrape → inject content + bullet-point instruction.
Post: pass-through (response goes straight to history).
"""

from __future__ import annotations

from lyra.core.processor_registry import register
from lyra.core.processors._scraping import ScrapingProcessor

_INSTRUCTION = (
    "Please summarize the following web content in 3-5 bullet points. Be concise."
)


@register(
    "/summarize",
    description="Summarize a URL in bullet points: /summarize <url>",
)
class SummarizeProcessor(ScrapingProcessor):
    """Scrape URL → inject content → LLM summarizes as bullet points."""

    @property
    def instruction(self) -> str:
        return _INSTRUCTION
