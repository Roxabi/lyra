"""_ProcessEntry dataclass — isolated to break the circular import chain.

cli_non_streaming and cli_streaming both need _ProcessEntry for type
annotations, but they also transitively import cli_protocol_types.
Moving the dataclass here (no cli_protocol* dependencies) lets both modules
import it directly without a cycle.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field

from ..agent.agent_config import ModelConfig


@dataclass
class _ProcessEntry:  # pyright: ignore[reportUnusedClass]
    """A persistent CLI process for one pool."""

    proc: asyncio.subprocess.Process
    pool_id: str
    model_config: ModelConfig
    system_prompt: str = ""
    session_id: str | None = None
    resumed_from: str | None = None  # session_id passed to --resume at spawn
    # tmpfile for --system-prompt-file (cleaned on kill)
    prompt_file: str | None = None
    turn_count: int = 0
    last_activity: float = field(default_factory=time.time)
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _on_session_update: Callable[[str, str], None] | None = field(
        default=None, repr=False
    )

    def is_alive(self) -> bool:
        return self.proc.returncode is None

    def update_session_id(self, sid: str | None) -> None:
        """Set session_id and fire the persist callback if changed."""
        import logging

        log = logging.getLogger(__name__)
        if sid and sid != self.session_id:
            self.session_id = sid
            if self._on_session_update is not None:
                try:
                    self._on_session_update(self.pool_id, sid)
                except Exception:
                    log.debug("[pool:%s] session update callback failed", self.pool_id)
