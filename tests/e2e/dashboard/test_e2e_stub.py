"""Dashboard E2E stub mode structural tests (#1771)."""

from __future__ import annotations

import os

from factory.dashboard.e2e import e2e_enabled, stub_sessions_list


def test_e2e_flag() -> None:
    os.environ["FACTORY_DASHBOARD_E2E"] = "1"
    assert e2e_enabled() is True
    os.environ.pop("FACTORY_DASHBOARD_E2E", None)


def test_stub_sessions_have_platform_tags() -> None:
    rows = stub_sessions_list("lyra").sessions
    platforms = {r.platform for r in rows}
    assert "web" in platforms
    assert "telegram" in platforms