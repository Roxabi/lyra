"""Add scripts/dep-graph to sys.path so dep_graph is importable from the lyra root."""

import sys
from pathlib import Path

_DEP_GRAPH_SRC = Path(__file__).parent.parent.parent / "scripts" / "dep-graph"
if str(_DEP_GRAPH_SRC) not in sys.path:
    sys.path.insert(0, str(_DEP_GRAPH_SRC))
