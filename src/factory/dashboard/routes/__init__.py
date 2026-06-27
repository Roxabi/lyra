"""Dashboard HTTP route modules (BFF axis only)."""

from .bff import build_bff_router

__all__ = ["build_bff_router"]