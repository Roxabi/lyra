"""Shared test helpers package.

Re-exports plain utility functions from individual modules. Kept free of
pytest fixtures — plain functions only so they can be imported anywhere.
"""

from __future__ import annotations


def reload_processors() -> None:
    """Force reload of processor submodules to re-trigger @register decorators.

    The global registry may have been cleared by a preceding conftest
    (tests/core/processors/conftest.py autouse fixture).  Python caches
    imports, so a plain ``import lyra.core.processors`` is a no-op after
    the first import.  We must explicitly reload each submodule.
    """
    import importlib

    import lyra.core.processors
    import lyra.core.processors.explain
    import lyra.core.processors.search
    import lyra.core.processors.summarize
    import lyra.core.processors.vault_add
    from lyra.core.processor_registry import registry

    registry.clear()
    importlib.reload(lyra.core.processors.explain)
    importlib.reload(lyra.core.processors.search)
    importlib.reload(lyra.core.processors.summarize)
    importlib.reload(lyra.core.processors.vault_add)
    importlib.reload(lyra.core.processors)


from tests.helpers.messages import make_text_message, make_voice_message  # noqa: E402

__all__ = ["reload_processors", "make_text_message", "make_voice_message"]
