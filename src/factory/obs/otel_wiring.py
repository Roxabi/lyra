"""Factory composition root for NATS worker OTel hooks (#2069).

``ObservabilityProvider`` in ``base.py`` remains the Langfuse-shaped abstraction
for future dashboard composition; worker span export uses ``roxabi-otel`` hooks.
"""

from __future__ import annotations

import os

from roxabi_otel import NoopHooks, OtelLifecycleHooks, otel_enabled

from roxabi_contracts.telemetry import MessageLifecycleHooks


def build_lifecycle_hooks(service_name: str) -> MessageLifecycleHooks:
    """Return OTel hooks or Noop based on ``ROXABI_OTEL_ENABLED``."""
    if not otel_enabled():
        return NoopHooks()
    endpoint = os.environ.get("OTEL_EXPORTER_OTLP_ENDPOINT", "").strip()
    if not endpoint and os.environ.get("ROXABI_OTEL_REQUIRE_ENDPOINT", "").strip() in {
        "1",
        "true",
    }:
        return NoopHooks()
    return OtelLifecycleHooks(service_name=service_name)