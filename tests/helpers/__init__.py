"""Shared test helpers package.

Re-exports plain utility functions from individual modules. Kept free of
pytest fixtures — plain functions only so they can be imported anywhere.
"""

from __future__ import annotations


def reload_processors() -> None:
    """Force reload of processor submodules to re-trigger @register decorators.

    The global registry may have been cleared by a preceding conftest
    (tests/core/processors/conftest.py autouse fixture).  Python caches
    imports, so a plain ``import factory.core.processors`` is a no-op after
    the first import.  We must explicitly reload each submodule.
    """
    import importlib

    import factory.core.processors
    import factory.core.processors.explain
    import factory.core.processors.search
    import factory.core.processors.summarize
    import factory.core.processors.vault_add
    from factory.core.processors.processor_registry import registry

    registry.clear()
    importlib.reload(factory.core.processors.explain)
    importlib.reload(factory.core.processors.search)
    importlib.reload(factory.core.processors.summarize)
    importlib.reload(factory.core.processors.vault_add)
    importlib.reload(factory.core.processors)


from tests.helpers.messages import make_text_message, make_voice_message  # noqa: E402

__all__ = ["reload_processors", "make_text_message", "make_voice_message"]
