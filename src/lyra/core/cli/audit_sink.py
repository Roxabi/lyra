"""AuditSink protocol — consumed by CliPool; implemented in lyra.infrastructure."""

from __future__ import annotations

from typing import Protocol

from roxabi_contracts.audit import SecurityEvent


class AuditSink(Protocol):
    async def emit(self, event: SecurityEvent) -> None: ...
