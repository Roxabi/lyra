"""Tests for Loki/Promtail observability stack deploy artifacts."""

from __future__ import annotations

from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
OBS_DIR = REPO_ROOT / "deploy" / "observability"
QUADLET_DIR = REPO_ROOT / "deploy" / "quadlet"
QUADLET_TOML = REPO_ROOT / "deploy" / "quadlet.toml"


def test_loki_config_is_valid_yaml() -> None:
    data = yaml.safe_load((OBS_DIR / "loki-config.yml").read_text())
    assert data["server"]["http_listen_port"] == 3100
    assert data["limits_config"]["retention_period"] == "744h"


def test_promtail_config_targets_loki_and_sources() -> None:
    data = yaml.safe_load((OBS_DIR / "promtail-config.yml").read_text())
    assert data["clients"][0]["url"] == "http://factory-loki:3100/loki/api/v1/push"
    jobs = {item["job_name"] for item in data["scrape_configs"]}
    assert jobs == {"factory-operator", "factory-journal"}


def test_quadlet_units_exist() -> None:
    for name in (
        "factory-loki.container",
        "factory-promtail.container",
        "factory-loki-data.volume",
    ):
        assert (QUADLET_DIR / name).is_file()


def test_quadlet_toml_declares_loki_and_promtail() -> None:
    text = QUADLET_TOML.read_text()
    assert 'container = "factory-loki.container"' in text
    assert 'container = "factory-promtail.container"' in text
    assert 'volume = "factory-loki-data.volume"' in text