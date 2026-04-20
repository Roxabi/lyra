"""CLI entry: build v5 HTML and write to the visuals dir."""
from __future__ import annotations

import sys
from pathlib import Path

_HERE = Path(__file__).resolve().parent
_PARENT = _HERE.parent
if str(_PARENT) not in sys.path:
    sys.path.insert(0, str(_PARENT))

from v5 import compose  # noqa: E402
from v5.data import load as loader  # noqa: E402

OUT = (
    Path.home()
    / ".roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v5.1.html"
)


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    active = "graph"
    if "--active=grid" in argv:
        active = "grid"
    data = loader.load()
    size = compose.write(OUT, data, active=active)
    print(f"wrote {OUT} ({size:,} bytes) · active={active}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
