"""lyra agent_cmd platforms package — import to trigger registration."""

from __future__ import annotations

import importlib

importlib.import_module("lyra.agent_cmd.platforms.telegram")
importlib.import_module("lyra.agent_cmd.platforms.discord")
