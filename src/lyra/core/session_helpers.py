"""Subprocess helpers for session commands (issue #99).

Provides async wrappers around external CLI tools:
  - web-intel:scrape  — fetches and converts web content to text
  - vault             — reads/writes to the personal knowledge base
"""

from __future__ import annotations

import asyncio
from asyncio.subprocess import PIPE


class ScrapeFailed(Exception):
    """Raised when web-intel:scrape fails or is not available."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class VaultWriteFailed(Exception):
    """Raised when the vault CLI write fails or is not available."""


async def scrape_url(url: str, timeout: float = 30.0) -> str:
    """Run web-intel:scrape <url> and return the decoded stdout.

    Raises:
        ScrapeFailed("not_available")  — if web-intel:scrape is not on PATH.
        ScrapeFailed("subprocess_error") — if the process exits non-zero.
        asyncio.TimeoutError — if the subprocess takes longer than timeout.
    """
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                "web-intel:scrape", url, stdout=PIPE, stderr=PIPE
            ),
            timeout=timeout,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            raise ScrapeFailed("subprocess_error")
        return stdout.decode()
    except FileNotFoundError:
        raise ScrapeFailed("not_available")


async def vault_add(
    title: str,
    tags: list[str],
    url: str,
    body: str,
    timeout: float = 30.0,
) -> None:
    """Run vault add ... to persist content to the knowledge base.

    Raises:
        VaultWriteFailed("not_available")    — if vault is not on PATH.
        VaultWriteFailed("subprocess_error") — if the process exits non-zero.
        asyncio.TimeoutError — if the subprocess takes longer than timeout.
    """
    args = ["vault", "add", "--title", title, "--url", url, "--body", body]
    if tags:
        args += ["--tags", ",".join(tags)]
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(*args, stdout=PIPE, stderr=PIPE),
            timeout=timeout,
        )
        await proc.communicate()
        if proc.returncode != 0:
            raise VaultWriteFailed("subprocess_error")
    except FileNotFoundError:
        raise VaultWriteFailed("not_available")


async def vault_search(query: str, timeout: float = 30.0) -> str:
    """Run vault search <query> and return the decoded stdout.

    Returns a graceful string (no exception) when vault is absent or returns
    no results — search failure is not fatal.
    """
    try:
        proc = await asyncio.wait_for(
            asyncio.create_subprocess_exec(
                "vault", "search", query, stdout=PIPE, stderr=PIPE
            ),
            timeout=timeout,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode != 0:
            return "Search returned no results."
        return stdout.decode()
    except FileNotFoundError:
        return "vault CLI not available."
