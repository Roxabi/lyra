"""Dashboard HTTP route modules (BFF axis only).

Lazy exports avoid circular imports: ``auth`` → ``routes.hub_auth`` must not
load ``bff`` (which depends on ``auth``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["build_bff_router", "build_connectors_router"]


def __getattr__(name: str) -> Any:
    if name == "build_bff_router":
        from .bff import build_bff_router

        return build_bff_router
    if name == "build_connectors_router":
        from .connectors import build_connectors_router

        return build_connectors_router
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
