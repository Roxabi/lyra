"""Tests for the hub Prometheus `/metrics` endpoint (#1765, ADR-092 plane ③).

Two layers:
- ``TestMetricsEndpoint`` exercises the live ASGI route on a fresh ``Hub`` —
  status, content-type, unauthenticated access (no regression on /health), and
  that the always-present gauges render + parse.
- ``TestRenderPrometheus`` unit-tests ``_render_prometheus`` against a fully
  populated snapshot to cover the labeled/optional gauges (per-platform queues,
  circuits, nats, reaper) without wiring hub internals.
"""

from __future__ import annotations

from unittest.mock import Mock

from httpx import ASGITransport, AsyncClient

from factory.bootstrap.infra.health import (
    Secrets,
    _render_prometheus,
    create_health_app,
)
from factory.core.hub import Hub

_ALWAYS_PRESENT = (
    "factory_hub_queue_size",
    "factory_uptime_seconds",
    "factory_adapters_total",
    "factory_bus_subscriptions_total",
)


def _parse_samples(text: str) -> dict[str, float]:
    """Parse Prometheus exposition text → {series: value}, validating each line."""
    samples: dict[str, float] = {}
    for line in text.splitlines():
        if not line or line.startswith("#"):
            continue
        series, _, raw = line.rpartition(" ")
        assert series, f"unparseable metric line: {line!r}"
        samples[series] = float(raw)  # raises if the value is not a float
    return samples


class TestMetricsEndpoint:
    async def test_metrics_returns_prometheus_text(self) -> None:
        app = create_health_app(Hub())
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")

        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/plain")
        body = resp.text
        for name in _ALWAYS_PRESENT:
            assert f"# TYPE {name} gauge" in body
        samples = _parse_samples(body)
        for name in _ALWAYS_PRESENT:
            assert name in samples

    async def test_metrics_requires_no_auth(self) -> None:
        # Prometheus scrapes unauthenticated; /health/detail auth is unaffected.
        app = create_health_app(Hub(), secrets=Mock(spec=Secrets, health_secret="s"))
        transport = ASGITransport(app=app)
        async with AsyncClient(transport=transport, base_url="http://test") as client:
            resp = await client.get("/metrics")
        assert resp.status_code == 200


class TestRenderPrometheus:
    def test_labeled_and_optional_gauges(self) -> None:
        detail = {
            "ok": True,
            "queue_size": 4,
            "queues": {
                "inbound": {"telegram": 1, "discord": 2},
                "outbound": {"telegram": 0},
            },
            "last_message_age_s": 12.5,
            "uptime_s": 99.0,
            "circuits": {
                "anthropic": {"state": "open", "retry_after": 30.0},
                "litellm": {"state": "closed", "retry_after": None},
            },
            "adapters": 2,
            "buses": 3,
            "nats": "ok",
            "reaper_alive": True,
            "reaper_last_sweep_age": 5.0,
        }

        text = _render_prometheus(detail)
        samples = _parse_samples(text)

        assert samples['factory_inbound_queue_depth{platform="telegram"}'] == 1
        assert samples['factory_inbound_queue_depth{platform="discord"}'] == 2
        assert samples['factory_outbound_queue_depth{platform="telegram"}'] == 0
        # state codes: open=2, closed=0
        assert samples['factory_circuit_state{circuit="anthropic"}'] == 2
        assert samples['factory_circuit_state{circuit="litellm"}'] == 0
        # retry_after only emitted for the non-None circuit
        assert (
            samples['factory_circuit_retry_after_seconds{circuit="anthropic"}'] == 30.0
        )
        assert 'factory_circuit_retry_after_seconds{circuit="litellm"}' not in samples
        assert samples["factory_nats_up"] == 1
        assert samples["factory_reaper_alive"] == 1
        assert samples["factory_reaper_last_sweep_age_seconds"] == 5.0
        assert samples["factory_last_message_age_seconds"] == 12.5

    def test_omits_absent_optional_gauges(self) -> None:
        detail = {
            "ok": True,
            "queue_size": 0,
            "queues": {"inbound": {}, "outbound": {}},
            "last_message_age_s": None,
            "uptime_s": 1.0,
            "circuits": {},
            "adapters": 0,
            "buses": 0,
        }
        text = _render_prometheus(detail)
        assert "factory_nats_up" not in text
        assert "factory_reaper_alive" not in text
        assert "factory_last_message_age_seconds" not in text
        assert "factory_inbound_queue_depth" not in text
        # core gauges still present
        assert "factory_hub_queue_size 0" in text
