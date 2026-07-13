"""Architecture: web adapter / dashboard process must not open auth.db identity."""

from __future__ import annotations

from pathlib import Path


def test_no_open_control_plane_store_in_web_or_dashboard_prod_paths() -> None:
    root = Path(__file__).resolve().parents[2] / "src" / "factory"
    offenders: list[str] = []
    for rel in (
        "adapters/web/web_adapter.py",
        "dashboard/app.py",
        "dashboard/auth.py",
        "dashboard/routes/hub_auth.py",
        "dashboard/routes/auth_routes.py",
        "dashboard/routes/auth_admin_routes.py",
        "dashboard/routes/auth_common.py",
    ):
        text = (root / rel).read_text(encoding="utf-8")
        if "open_control_plane_store" in text:
            offenders.append(rel)
        if "ControlPlaneStore(" in text and "TYPE_CHECKING" not in text:
            # Instantiation of store in these modules is dual-open debt
            if "ControlPlaneStore(" in text:
                offenders.append(f"{rel}:ControlPlaneStore(")
    assert offenders == [], f"dual-open surface: {offenders}"
