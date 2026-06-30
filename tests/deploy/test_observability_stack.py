"""Tests for Loki/Promtail/OTel/Langfuse observability stack deploy artifacts."""

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


def test_otel_collector_config_exports_jsonl() -> None:
    data = yaml.safe_load((OBS_DIR / "otel-collector-config.yml").read_text())
    assert data["receivers"]["otlp"]["protocols"]["grpc"]["endpoint"] == "0.0.0.0:4317"
    assert data["exporters"]["file"]["path"] == "/otel-data/spans.jsonl"
    assert "otlphttp/langfuse" not in data.get("exporters", {})
    assert "health_check" in data["extensions"]


def test_quadlet_toml_langfuse_components_disabled() -> None:
    text = QUADLET_TOML.read_text()
    for name in (
        "langfuse-postgres",
        "langfuse-clickhouse",
        "langfuse-redis",
        "langfuse-minio",
        "langfuse-worker",
        "langfuse-web",
    ):
        section = f"[component.{name}]"
        start = text.index(section)
        block = text[start : text.find("\n[", start + 1)]
        assert "disabled = true" in block


def test_quadlet_units_exist() -> None:
    for name in (
        "factory-loki.container",
        "factory-promtail.container",
        "factory-loki-data.volume",
        "factory-otel-collector.container",
        "factory-langfuse-web.container",
        "factory-langfuse-worker.container",
        "factory-langfuse-postgres.container",
        "factory-langfuse-clickhouse.container",
        "factory-langfuse-redis.container",
        "factory-langfuse-minio.container",
        "factory-langfuse-postgres-data.volume",
        "factory-langfuse-minio-data.volume",
    ):
        assert (QUADLET_DIR / name).is_file()


def test_quadlet_toml_declares_observability_stack() -> None:
    text = QUADLET_TOML.read_text()
    for fragment in (
        'container = "factory-loki.container"',
        'container = "factory-promtail.container"',
        'container = "factory-otel-collector.container"',
        'container = "factory-langfuse-web.container"',
        'volume = "factory-langfuse-postgres-data.volume"',
    ):
        assert fragment in text


def test_bootstrap_langfuse_script_exists() -> None:
    script = REPO_ROOT / "deploy" / "scripts" / "bootstrap-langfuse.sh"
    assert script.is_file()
    assert script.stat().st_mode & 0o111