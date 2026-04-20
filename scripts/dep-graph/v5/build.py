"""CLI entry: build v5 HTML and write to the visuals dir.

Run from the ``scripts/dep-graph`` directory::

    python -m v5.build [--active=grid]
"""
from __future__ import annotations

import argparse
from pathlib import Path

from . import compose
from .data import load as loader

OUT = (
    Path.home()
    / ".roxabi/forge/lyra/visuals/lyra-v2-dependency-graph-v5.1.html"
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="v5.build")
    parser.add_argument(
        "--active",
        choices=("graph", "grid"),
        default="graph",
        help="Which view is active on first paint.",
    )
    args = parser.parse_args(argv)

    data = loader.load()
    size = compose.write(OUT, data, active=args.active)
    print(f"wrote {OUT} ({size:,} bytes) · active={args.active}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
