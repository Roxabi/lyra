"""Subprocess helpers for session commands (issue #99).

Provides async wrappers around external CLI tools:
  - web-intel scraper  — fetches and converts web content to text
  - vault              — reads/writes to the personal knowledge base

web-intel is an external plugin (roxabi-plugins) with its own venv and heavy
deps (playwright, trafilatura, …). It cannot be imported directly — Lyra spawns
it as a subprocess via `uv run python scripts/scraper.py <url>` inside the
plugin directory.

Override the plugin root with LYRA_WEB_INTEL_PATH env var.
Default: ~/projects/roxabi-plugins/plugins/web-intel
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from asyncio.subprocess import PIPE
from pathlib import Path

log = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# web-intel path resolution
# ---------------------------------------------------------------------------


def _web_intel_path() -> Path:
    """Resolve the web-intel plugin root (machine-specific, overridable)."""
    raw = os.environ.get("LYRA_WEB_INTEL_PATH")
    if raw:
        return Path(raw).expanduser()
    return Path.home() / "projects" / "roxabi-plugins" / "plugins" / "web-intel"


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class ScrapeFailed(Exception):
    """Raised when the web-intel scraper fails or is not available."""

    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


class VaultWriteFailed(Exception):
    """Raised when the vault CLI write fails or is not available."""


# ---------------------------------------------------------------------------
# Scraper
# ---------------------------------------------------------------------------


async def scrape_url(url: str, timeout: float = 30.0) -> str:
    """Run the web-intel scraper and return extracted text content.

    Invokes: uv run python scripts/scraper.py <url>
    inside the web-intel plugin directory (separate venv, heavy deps).

    Returns the ``data.text`` field from the scraper's JSON output.

    Raises:
        ScrapeFailed("not_available")    — uv or scraper.py not found.
        ScrapeFailed("subprocess_error") — process exited non-zero or bad JSON.
        ScrapeFailed("timeout")          — subprocess exceeded timeout.
    """
    plugin_root = _web_intel_path()
    scraper = plugin_root / "scripts" / "scraper.py"

    try:
        proc = await asyncio.create_subprocess_exec(
            "uv", "run", "python", str(scraper), url,
            stdout=PIPE, stderr=PIPE,
            cwd=str(plugin_root),
        )
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise ScrapeFailed("timeout")
        if proc.returncode != 0:
            log.warning(
                "scrape_url: scraper exited %d: %s",
                proc.returncode, stderr.decode()[:200],
            )
            raise ScrapeFailed("subprocess_error")
    except FileNotFoundError:
        raise ScrapeFailed("not_available")

    try:
        result = json.loads(stdout.decode())
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        log.warning("scrape_url: failed to parse JSON output: %s", exc)
        raise ScrapeFailed("subprocess_error")

    if not result.get("success"):
        log.warning("scrape_url: scraper reported failure: %s", result.get("error"))
        raise ScrapeFailed("subprocess_error")

    text = result.get("data", {}).get("text", "")
    if not text:
        raise ScrapeFailed("subprocess_error")
    return text


# ---------------------------------------------------------------------------
# Vault
# ---------------------------------------------------------------------------


async def vault_add(
    title: str,
    tags: list[str],
    url: str,
    body: str,
    timeout: float = 30.0,
) -> None:
    """Run ``vault put`` to persist content to the knowledge base.

    Maps to: vault put <body> --title <title> --category references
                              --type bookmark --metadata <json>

    URL and tags are stored in --metadata (vault put has no --url/--tags flags).

    Raises:
        VaultWriteFailed("not_available")    — vault not on PATH.
        VaultWriteFailed("subprocess_error") — process exited non-zero.
        VaultWriteFailed("timeout")          — subprocess exceeded timeout.
    """
    metadata: dict[str, object] = {"url": url}
    if tags:
        metadata["tags"] = tags

    args = [
        "vault", "put", body,
        "--title", title,
        "--category", "references",
        "--type", "bookmark",
        "--metadata", json.dumps(metadata),
    ]
    try:
        proc = await asyncio.create_subprocess_exec(*args, stdout=PIPE, stderr=PIPE)
        try:
            _, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            raise VaultWriteFailed("timeout")
        if proc.returncode != 0:
            log.warning(
                "vault_add: vault exited %d: %s",
                proc.returncode, stderr.decode()[:200],
            )
            raise VaultWriteFailed("subprocess_error")
    except FileNotFoundError:
        raise VaultWriteFailed("not_available")


# ---------------------------------------------------------------------------
# Vault search (non-fatal)
# ---------------------------------------------------------------------------


async def vault_search(query: str, timeout: float = 30.0) -> str:
    """Run ``vault search <query>`` and return decoded stdout.

    Returns a graceful string (no exception) when vault is absent or returns
    no results — search failure is not fatal.
    """
    try:
        proc = await asyncio.create_subprocess_exec(
            "vault", "search", query, stdout=PIPE, stderr=PIPE
        )
        try:
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            proc.kill()
            return "vault search timed out."
        if proc.returncode != 0:
            return "Search returned no results."
        return stdout.decode()
    except FileNotFoundError:
        return "vault CLI not available."
