"""Fleet catalog loader tests."""

from __future__ import annotations

from factory.nats.fleet_catalog import _repo_root, load_fleet_catalog


def test_repo_root_resolves_deploy_quadlet_toml() -> None:
    root = _repo_root()
    assert (root / "deploy" / "quadlet.toml").is_file()


def test_load_fleet_catalog_includes_template_adapters() -> None:
    root = _repo_root()
    entries = load_fleet_catalog(
        quadlet_toml=root / "deploy" / "quadlet.toml",
        quadlet_dir=root / "deploy" / "quadlet",
    )
    names = {e.container_name for e in entries}
    assert "factory-telegram" in names
    assert "factory-discord" in names
    assert "factory-loki" in names
    assert len(entries) >= 19


def test_fleet_store_default_catalog_loads_from_repo() -> None:
    from factory.nats.fleet_store import FleetStore

    store = FleetStore()
    rows = store.list_snapshot()
    assert len(rows) >= 19
    loki = next(r for r in rows if r.container_name == "factory-loki")
    assert loki.instrumented is False
    assert loki.status in {"unknown", "pinned"}