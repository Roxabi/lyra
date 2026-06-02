"""Backward-compat shim — BaseFormatter moved to factory.outbound.formatter.

ADR-073 §Decision: stage primitives live in their stage module.
``BaseFormatter`` is a stage (outbound) concern consumed by ``OutboundEmitter``;
it belongs in ``factory.outbound``, not in ``factory.adapters.shared``.

Import from ``factory.outbound.formatter`` directly in new code.
"""

from factory.outbound.formatter import BaseFormatter

__all__ = ["BaseFormatter"]
