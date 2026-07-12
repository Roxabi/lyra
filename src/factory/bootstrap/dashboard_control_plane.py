"""Re-export control-plane open helper for bootstrap/CLI composition roots."""

from __future__ import annotations

from factory.infrastructure.stores.identity.control_plane_store import (
    open_control_plane_store as open_control_plane_for_dashboard,
)

__all__ = ["open_control_plane_for_dashboard"]
