"""Agent loader — DB-only path after #346 cleanup.

The TOML loading path (load_agent_config) was removed in #346.
Only agent_row_to_config (DB path) remains.
"""

from __future__ import annotations

# Re-export for backward compatibility — callers that imported from here.
from .agent_db_loader import agent_row_to_config as agent_row_to_config  # noqa: PLC0414

__all__ = ["agent_row_to_config"]
