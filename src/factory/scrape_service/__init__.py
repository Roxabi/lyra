"""HTTP scrape service — isolated provider for URL → text (issue #2327)."""

from __future__ import annotations

__all__ = ["build_app"]


def __getattr__(name: str):
    if name == "build_app":
        from factory.scrape_service.app import build_app

        return build_app
    raise AttributeError(name)
