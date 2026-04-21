"""Processor package — import all processors to trigger self-registration (issue #363).

Importing this package registers all built-in processors with the module-level
``lyra.core.processors.processor_registry.registry`` singleton.  New processors
are added by creating a new module here and importing it below — no other file
changes needed.
"""

from . import _scraping, explain, search, summarize, vault_add
from .processor_registry import BaseProcessor, ProcessorRegistry, registry
from .stream_processor import StreamProcessor

__all__ = [
    "_scraping",
    "explain",
    "search",
    "summarize",
    "vault_add",
    "BaseProcessor",
    "ProcessorRegistry",
    "StreamProcessor",
    "registry",
]
