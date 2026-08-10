"""HTTP scrape service — isolated provider for URL → text (issue #2327)."""

from __future__ import annotations

from factory.scrape_service.app import build_app

__all__ = ["build_app"]
