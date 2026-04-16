"""lyra agent CRUD commands — split into per-command modules.

This package registers commands on ``agent_app`` via decorator side-effects.
Import this module to trigger registration.
"""

from __future__ import annotations

import importlib

# Import sub-modules to register their commands on agent_app
importlib.import_module("lyra.agent_cmd.agents.init")
importlib.import_module("lyra.agent_cmd.agents.list_cmd")
importlib.import_module("lyra.agent_cmd.agents.edit_cmd")
