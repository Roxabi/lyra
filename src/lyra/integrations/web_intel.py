"""WebIntelScraper — ScrapeProvider backed by the roxabi web-intel plugin (issue #360).

Invokes: uv run python scripts/scraper.py <url>
inside the web-intel plugin directory (separate venv: playwright, trafilatura).

Override plugin root with LYRA_WEB_INTEL_PATH env var.
Default: ~/projects/roxabi-plugins/plugins/web-intel
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
from asyncio.subprocess import PIPE
from pathlib import Path

from lyra.core.exceptions import ScrapeFailed

log = logging.getLogger(__name__)


class WebIntelScraper:
    """ScrapeProvider backed by the web-intel roxabi plugin."""

    _TRUSTED_BASE = Path.home() / "projects"

    def __init__(self, plugin_root: Path | None = None) -> None:
        raw = os.environ.get("LYRA_WEB_INTEL_PATH")
        if raw:
            resolved = Path(raw).expanduser().resolve()
            if not resolved.is_relative_to(self._TRUSTED_BASE):
                raise ValueError(
                    f"LYRA_WEB_INTEL_PATH resolves to {resolved!r}, "
                    f"which is outside the trusted base {self._TRUSTED_BASE!r}."
                )
            self._root = resolved
        else:
            self._root = plugin_root or (
                Path.home() / "projects" / "roxabi-plugins" / "plugins" / "web-intel"
            )

    async def scrape(self, url: str, timeout: float = 30.0) -> str:
        """Run web-intel scraper and return extracted text content.

        Raises:
            ScrapeFailed("not_available")    — uv or scraper.py not found.
            ScrapeFailed("subprocess_error") — non-zero exit or bad JSON.
            ScrapeFailed("timeout")          — subprocess exceeded timeout.
        """
        scraper = self._root / "scripts" / "scraper.py"
        try:
            proc = await asyncio.create_subprocess_exec(
                "uv",
                "run",
                "python",
                str(scraper),
                url,
                stdout=PIPE,
                stderr=PIPE,
                cwd=str(self._root),
            )
            try:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout
                )
            except asyncio.TimeoutError:
                proc.kill()
                await proc.wait()
                raise ScrapeFailed("timeout")
            if proc.returncode != 0:
                log.warning(
                    "WebIntelScraper: exited %d: %s",
                    proc.returncode,
                    stderr.decode()[:200],
                )
                raise ScrapeFailed("subprocess_error")
        except FileNotFoundError:
            raise ScrapeFailed("not_available")

        try:
            result = json.loads(stdout.decode())
        except (json.JSONDecodeError, UnicodeDecodeError) as exc:
            log.warning("WebIntelScraper: failed to parse JSON: %s", exc)
            raise ScrapeFailed("subprocess_error")

        if not result.get("success"):
            log.warning(
                "WebIntelScraper: scraper reported failure: %s", result.get("error")
            )
            raise ScrapeFailed("subprocess_error")

        text = result.get("data", {}).get("text", "")
        if not text:
            raise ScrapeFailed("subprocess_error")
        return text
