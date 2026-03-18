"""Tool provider Protocols and shared types for session commands (issue #360).

Mirrors the LlmProvider pattern in lyra.llm.base:
  - Protocols define the interface (ScrapeProvider, VaultProvider)
  - Implementations live in sibling modules (web_intel, vault_cli)
  - SessionTools bundles providers for injection at session command registration

VaultProvider.search intentionally does NOT raise — search failure is non-fatal.
VaultProvider.add raises VaultWriteFailed — write failure is actionable.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


class ScrapeFailed(Exception):
    """Raised when the scraper fails or is not available."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class VaultWriteFailed(Exception):
    """Raised when the vault write fails or is not available."""

    def __init__(self, reason: str = "") -> None:
        super().__init__(reason)


@runtime_checkable
class ScrapeProvider(Protocol):
    """Async scraper: fetch URL → return text content."""

    async def scrape(self, url: str, timeout: float = 30.0) -> str: ...


@runtime_checkable
class VaultProvider(Protocol):
    """Async vault access: write and search the knowledge base."""

    async def add(
        self,
        title: str,
        tags: list[str],
        url: str,
        body: str,
        timeout: float = 30.0,
    ) -> None: ...

    async def search(self, query: str, timeout: float = 30.0) -> str: ...


@dataclass
class SessionTools:
    """Bundle of tool providers injected into session commands at registration."""

    scraper: ScrapeProvider
    vault: VaultProvider
