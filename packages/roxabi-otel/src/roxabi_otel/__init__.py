"""roxabi_otel — OTel lifecycle hooks for NATS workers."""

from .hooks import InMemorySpanRecorder, NoopHooks, OtelLifecycleHooks, otel_enabled
from .scrub import scrub_attrs

__all__ = [
    "InMemorySpanRecorder",
    "NoopHooks",
    "OtelLifecycleHooks",
    "otel_enabled",
    "scrub_attrs",
]