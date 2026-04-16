"""Session persistence mixin for CliPool — split from cli_pool.py (#760).

Provides TurnStore wiring and CLI session ID persistence for --resume support.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .stores.turn_store import TurnStore

from .cli_protocol import _SESSION_ID_RE

log = logging.getLogger(__name__)


class CliPoolSessionMixin:
    """Mixin providing CLI session persistence for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _lyra_sessions: dict[str, str]
        _turn_store: "TurnStore | None"

    def set_turn_store(self, store: TurnStore) -> None:
        """Wire the TurnStore for CLI session persistence across restarts."""
        self._turn_store = store

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        """Associate the current Lyra session UUID with *pool_id*.

        Called by the agent before each send so the persist callback can
        map ``lyra_session_id → cli_session_id`` (for reply-to-resume).
        """
        self._lyra_sessions[pool_id] = lyra_session_id

    def _persist_cli_session(self, pool_id: str, cli_session_id: str) -> None:
        """Persist CLI session ID to TurnStore for --resume after daemon restart."""
        if not cli_session_id or not _SESSION_ID_RE.match(cli_session_id):
            return
        lyra_sid = self._lyra_sessions.get(pool_id)
        if lyra_sid and self._turn_store is not None:
            try:
                asyncio.get_running_loop().create_task(
                    self._turn_store.set_cli_session(lyra_sid, cli_session_id)
                )
            except RuntimeError:
                pass  # no running loop — test context without TurnStore
