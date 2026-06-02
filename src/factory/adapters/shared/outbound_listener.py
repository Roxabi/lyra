"""Backward-compatibility shim for OutboundListener.

OutboundListener has been relocated to factory.core.ports.outbound_listener
(ADR-073 / #1666). This module re-exports it to avoid breaking existing
adapter imports while the inbound-no-adapters contract is now satisfied.
"""

from factory.core.ports.outbound_listener import OutboundListener

__all__ = ["OutboundListener"]
