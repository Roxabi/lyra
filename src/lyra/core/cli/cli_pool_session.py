"""Session persistence mixin for CliPool — split from cli_pool.py (#760).

Provides TurnPublisher wiring and CLI session ID persistence for --resume support.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from lyra.core.stores import TurnStoreProtocol
    from lyra.transport.turn_publisher import TurnPublisher

from .protocol.cli_protocol import SESSION_ID_RE

log = logging.getLogger(__name__)


class CliPoolSessionMixin:
    """Mixin providing CLI session persistence for CliPool."""

    # Declared for type-checking — initialised by CliPool.__init__.
    if TYPE_CHECKING:
        _lyra_sessions: dict[str, str]
        _turn_store: "TurnStoreProtocol | None"
        _turn_publisher: "TurnPublisher | None"

    def set_turn_store(self, store: TurnStoreProtocol) -> None:
        """Wire the TurnStore for CLI session reads (get_cli_session, etc.)."""
        self._turn_store = store

    def set_turn_publisher(self, publisher: TurnPublisher) -> None:
        """Wire the TurnPublisher for NATS-backed CLI session writes."""
        self._turn_publisher = publisher

    def link_lyra_session(self, pool_id: str, lyra_session_id: str) -> None:
        """Associate the current Lyra session UUID with *pool_id*.

        Called by the agent before each send so the persist callback can
        map ``lyra_session_id → cli_session_id`` (for reply-to-resume).
        """
        self._lyra_sessions[pool_id] = lyra_session_id

    def _persist_cli_session(self, pool_id: str, cli_session_id: str) -> None:
        """Persist CLI session ID via TurnPublisher for --resume after restart."""
        if not cli_session_id or not SESSION_ID_RE.match(cli_session_id):
            return
        lyra_sid = self._lyra_sessions.get(pool_id)
        publisher = getattr(self, "_turn_publisher", None)
        if lyra_sid and publisher is not None:
            try:
                loop = asyncio.get_running_loop()
                # trace_id: use lyra_sid as session correlation key
                loop.create_task(
                    publisher.publish_set_cli_session(
                        pool_id=pool_id,
                        session_id=lyra_sid,
                        platform="",  # not available in CliPool context
                        user_id="",  # not available in CliPool context
                        cli_session_id=cli_session_id,
                        trace_id=lyra_sid,
                    )
                )
            except RuntimeError:
                pass  # no running loop — test context without TurnPublisher
