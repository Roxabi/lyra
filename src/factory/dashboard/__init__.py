"""Control-plane BFF — obs/jobs panels (ADR-094 axis 2).

Serves built SPA from ``apps/dashboard-v2/dist/`` in production.
Chat transport stays in ``factory.adapters.web``. See ``AGENTS.md``.
"""

from factory.dashboard.app import create_dashboard_app

__all__ = ["create_dashboard_app"]
