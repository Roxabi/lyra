"""Control-plane BFF — obs/jobs panels (ADR-094 axis 2).

Serves built SPA from ``apps/dashboard-v2/dist/`` in production.
Chat transport stays in ``factory.adapters.web``. See ``AGENTS.md``.

``create_dashboard_app`` is a thin lazy re-export so adapters can import
``factory.dashboard.auth`` without loading ``app`` (avoids circular import
through ``app`` → ``chat_routes`` → ``dashboard.auth``).
"""

from __future__ import annotations

from typing import Any

__all__ = ["create_dashboard_app"]


def create_dashboard_app(*args: Any, **kwargs: Any) -> Any:
    """Lazy re-export of :func:`factory.dashboard.app.create_dashboard_app`."""
    from factory.dashboard.app import create_dashboard_app as _create

    return _create(*args, **kwargs)
