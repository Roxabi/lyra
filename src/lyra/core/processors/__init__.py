"""Processor package — import all processors to trigger self-registration (issue #363).

Importing this package registers all built-in processors with the module-level
``lyra.core.processor_registry.registry`` singleton.  New processors are added
by creating a new module here and importing it below — no other file changes needed.
"""

from . import _scraping, add_vault, explain, search, summarize, vault_add

__all__ = ["_scraping", "add_vault", "explain", "search", "summarize", "vault_add"]
