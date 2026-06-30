"""Dashboard HTTP route modules (BFF axis only)."""

from .bff import build_bff_router
from .connectors import build_connectors_router

__all__ = ["build_bff_router", "build_connectors_router"]