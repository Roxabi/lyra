"""VaultCli — VaultProvider backed by the vault CLI (issue #360).

Maps to:
  add   → vault put <body> --title --category references --type bookmark
           --metadata <json>
  search → vault search <query>

VaultProvider.search intentionally swallows all errors — search failure is non-fatal.
"""
from __future__ import annotations

import asyncio
import json
import logging
from asyncio.subprocess import PIPE

from lyra.integrations.base import VaultWriteFailed

log = logging.getLogger(__name__)


class VaultCli:
    """VaultProvider backed by the vault CLI."""

    async def add(
        self,
        title: str,
        tags: list[str],
        url: str,
        body: str,
        timeout: float = 30.0,
    ) -> None:
        """Run vault put to persist content.

        Raises:
            VaultWriteFailed("not_available")    — vault not on PATH.
            VaultWriteFailed("subprocess_error") — non-zero exit.
            VaultWriteFailed("timeout")          — exceeded timeout.
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
                    "VaultCli.add: exited %d: %s",
                    proc.returncode, stderr.decode()[:200],
                )
                raise VaultWriteFailed("subprocess_error")
        except FileNotFoundError:
            raise VaultWriteFailed("not_available")

    async def search(self, query: str, timeout: float = 30.0) -> str:
        """Run vault search; returns graceful string on any failure."""
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
