"""Backward-compatible re-export — otel-raw reader lives in factory.otel."""

from factory.otel.reader import OtelRawReader, SpanRow

__all__ = ["OtelRawReader", "SpanRow"]