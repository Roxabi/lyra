"""Slice 5: dual-open bootstrap helper must not open auth.db."""

from __future__ import annotations

import pytest

from factory.bootstrap.dashboard_control_plane import open_control_plane_for_dashboard


@pytest.mark.asyncio
async def test_open_control_plane_for_dashboard_raises() -> None:
    with pytest.raises(RuntimeError, match="removed"):
        await open_control_plane_for_dashboard()
