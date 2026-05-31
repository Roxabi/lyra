"""Processor package — import all processors to trigger self-registration (issue #363).

Importing this package registers all built-in processors with the module-level
``lyra.core.processors.processor_registry.registry`` singleton.  New processors
are added by creating a new module here and importing it below — no other file
changes needed.
"""

from . import _scraping, explain, search, summarize, vault_add
from .processor_registry import BaseProcessor, ProcessorRegistry, registry
from .stream_close import StreamCloseHandler
from .stream_processor import StreamProcessor
from .stream_text import (
    StreamTextHandler,
    _mint_reasoning_block_id,
    _mint_text_block_id,
)
from .stream_tool import StreamToolHandler, _sanitize_tool_result_content

__all__ = [
    "_scraping",
    "explain",
    "search",
    "summarize",
    "vault_add",
    "BaseProcessor",
    "ProcessorRegistry",
    "StreamCloseHandler",
    "StreamProcessor",
    "StreamTextHandler",
    "StreamToolHandler",
    "_mint_text_block_id",
    "_mint_reasoning_block_id",
    "_sanitize_tool_result_content",
    "registry",
]
