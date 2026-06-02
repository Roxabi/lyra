"""lyra bot commands — split into per-command modules.

This package registers commands on ``bot_app`` via decorator side-effects.
Import this module to trigger registration.
"""

from __future__ import annotations

import importlib

importlib.import_module("factory.agent_cmd.bots.init")
