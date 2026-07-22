"""CortexVault — VaultProvider backed by roxabi-cortex NATS satellite (MVP).

Maps VaultProvider to:
  add      → roxabi.memory.capture
  search   → roxabi.memory.query.search
  assemble → roxabi.memory.query.assemble (agent context injection)

Uses a short-lived NATS connection per call when no shared client is injected
(keeps SessionTools wiring free of hub bootstrap changes). Prefer injecting
``nc`` / a connected client later for production pooling.
"""

from __future__ import annotations

import logging
import os
from typing import TYPE_CHECKING

from factory.core.exceptions import VaultWriteFailed
from roxabi_contracts.memory import (
    SUBJECTS,
    AssembleResponse,
    CaptureResponse,
    SearchResponse,
    build_assemble_request,
    build_capture_request,
    build_search_request,
)

if TYPE_CHECKING:
    from nats.aio.client import Client as NATS

log = logging.getLogger(__name__)


class CortexVault:
    """VaultProvider via cortex-memory NATS subjects."""

    def __init__(
        self,
        *,
        nats_url: str | None = None,
        nc: "NATS | None" = None,
        default_timeout: float = 15.0,
    ) -> None:
        # No localhost default — set NATS_URL or inject nc (avoids smoke e2e hangs).
        if nats_url is not None:
            self._nats_url = nats_url
        else:
            self._nats_url = os.environ.get("NATS_URL")
        self._nc = nc
        self._default_timeout = default_timeout

    async def add(  # noqa: PLR0913 — DEBT:wiring-bootstrap-deps
        self,
        title: str,
        tags: list[str],
        url: str,
        body: str,
        timeout: float = 30.0,
        category: str = "references",
        entry_type: str = "bookmark",
    ) -> None:
        req = build_capture_request(
            title=title,
            body=body,
            category=category,
            entry_type=entry_type,
            url=url,
            tags=tags,
        )
        try:
            raw = await self._request(
                SUBJECTS.capture,
                req.model_dump_json().encode(),
                timeout=timeout or self._default_timeout,
            )
        except TimeoutError as exc:
            raise VaultWriteFailed("timeout") from exc
        except (TimeoutError, OSError, RuntimeError, ValueError) as exc:
            log.warning("CortexVault.add: NATS error: %s", exc)
            raise VaultWriteFailed("not_available") from exc

        try:
            resp = CaptureResponse.model_validate_json(raw)
        except (ValueError, TypeError, KeyError) as exc:
            raise VaultWriteFailed("subprocess_error") from exc
        if not resp.ok:
            raise VaultWriteFailed("subprocess_error")

    async def search(self, query: str, timeout: float = 30.0) -> str:
        req = build_search_request(query=query)
        try:
            raw = await self._request(
                SUBJECTS.query_search,
                req.model_dump_json().encode(),
                timeout=timeout or self._default_timeout,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open search is non-fatal
            log.warning("CortexVault.search: %s", exc)
            return ""

        try:
            resp = SearchResponse.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — fail-open
            return ""
        if not resp.ok:
            return ""
        lines: list[str] = []
        for hit in resp.hits:
            line = f"- [{hit.category}/{hit.entry_type}] {hit.title}"
            if hit.snippet:
                line += f"\n  {hit.snippet}"
            if hit.url:
                line += f"\n  {hit.url}"
            lines.append(line)
        return "\n".join(lines)

    async def assemble(
        self,
        *,
        goal: str | None = None,
        budget_tokens: int = 700,
        namespace: str | None = None,
        user_id: str | None = None,
        timeout: float = 2.0,
    ) -> str:
        """Return a context block for system-prompt injection.

        Fail-open: any error or empty result returns ``\"\"`` (ADR-087 —
        a turn without memory beats a blocked turn).
        """
        req = build_assemble_request(
            goal=goal,
            budget_tokens=budget_tokens,
            namespace=namespace,
            user_id=user_id,
        )
        try:
            raw = await self._request(
                SUBJECTS.query_assemble,
                req.model_dump_json().encode(),
                timeout=timeout if timeout > 0 else self._default_timeout,
            )
        except Exception as exc:  # noqa: BLE001 — fail-open ADR-087
            log.warning("CortexVault.assemble: %s", exc)
            return ""
        try:
            resp = AssembleResponse.model_validate_json(raw)
        except Exception:  # noqa: BLE001 — fail-open
            return ""
        if not resp.ok:
            return ""
        return (resp.text or "").strip()

    async def _request(self, subject: str, payload: bytes, *, timeout: float) -> bytes:
        if self._nc is not None:
            msg = await self._nc.request(subject, payload, timeout=timeout)
            return bytes(msg.data)

        if not self._nats_url:
            raise RuntimeError(
                "CortexVault: NATS_URL unset and no nc injected"
            )

        from roxabi_nats.connect import nats_connect

        nc = await nats_connect(self._nats_url)
        try:
            msg = await nc.request(subject, payload, timeout=timeout)
            return bytes(msg.data)
        finally:
            await nc.drain()
