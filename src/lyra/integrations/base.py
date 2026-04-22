"""Tool provider Protocols and shared types for session commands (issue #360).

Mirrors the LlmProvider pattern in lyra.llm.base:
  - Protocols define the interface (ScrapeProvider, VaultProvider, AudioConverter,
    ServiceManager)
  - Implementations live in sibling modules (web_intel, vault_cli, audio, supervisor)
  - SessionTools bundles providers for injection at session command registration

VaultProvider.search intentionally does NOT raise — search failure is non-fatal.
VaultProvider.add raises VaultWriteFailed — write failure is actionable.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol, runtime_checkable


@runtime_checkable
class ScrapeProvider(Protocol):
    """Async scraper: fetch URL → return text content."""

    async def scrape(self, url: str, timeout: float = 30.0) -> str: ...


@runtime_checkable
class VaultProvider(Protocol):
    """Async vault access: write and search the knowledge base."""

    async def add(  # noqa: PLR0913 — each param is a distinct vault field
        self,
        title: str,
        tags: list[str],
        url: str,
        body: str,
        timeout: float = 30.0,
        category: str = "references",
        entry_type: str = "bookmark",
    ) -> None: ...

    async def search(self, query: str, timeout: float = 30.0) -> str: ...


class AudioConversionFailed(Exception):
    """Raised when audio conversion (e.g. WAV→OGG) fails."""

    def __init__(
        self, reason: Literal["not_available", "timeout", "subprocess_error"]
    ) -> None:
        self.reason = reason
        super().__init__(reason)


class ServiceControlFailed(Exception):
    """Raised when a service control operation fails."""

    def __init__(
        self, reason: Literal["not_available", "timeout", "subprocess_error"]
    ) -> None:
        self.reason = reason
        super().__init__(reason)


@runtime_checkable
class AudioConverter(Protocol):
    """Async audio converter: WAV → OGG/Opus."""

    async def convert_wav_to_ogg(self, wav_path: Path, ogg_path: Path) -> None: ...


@runtime_checkable
class ServiceManager(Protocol):
    """Async service manager: control supervisor services."""

    async def control(self, action: str, service: str | None) -> str: ...


@dataclass
class SessionTools:
    """Bundle of tool providers injected into session commands at registration."""

    scraper: ScrapeProvider
    vault: VaultProvider
