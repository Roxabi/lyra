"""limits.py — shared byte-limit constants for factory.core.

Single source of truth for byte/character caps that are referenced
from multiple modules (e.g. agent_config, persona).  Import-light by
design: stdlib only.
"""

from __future__ import annotations

MAX_PROMPT_BYTES = 64 * 1024  # const-ok: shared byte-limit, single SSoT
