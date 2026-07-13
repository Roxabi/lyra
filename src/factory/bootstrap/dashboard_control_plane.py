"""Deprecated — dashboard must not open ControlPlaneStore (ADR-103 Slice 2+).

Hub owns durable identity. Thin BFF uses ``factory.dashboard.auth.*`` RPC.
This module remains only so historical imports fail loudly in prod paths.
"""

from __future__ import annotations

__all__ = ["open_control_plane_for_dashboard"]


async def open_control_plane_for_dashboard(*_a, **_kw):  # noqa: ANN002, ANN003
    """Removed dual-open helper — raise instead of opening auth.db on dashboard."""
    raise RuntimeError(
        "open_control_plane_for_dashboard removed (ADR-103 Slice 2): "
        "use hub factory.dashboard.auth.* RPCs; hub opens ControlPlaneStore only"
    )
