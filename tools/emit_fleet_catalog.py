#!/usr/bin/env python3
"""Emit fleet catalog JSON from deploy/quadlet.toml + .container files."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from factory.nats.fleet_catalog import load_fleet_catalog  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--quadlet-toml",
        type=Path,
        default=ROOT / "deploy" / "quadlet.toml",
    )
    parser.add_argument(
        "--quadlet-dir",
        type=Path,
        default=ROOT / "deploy" / "quadlet",
    )
    args = parser.parse_args()
    entries = load_fleet_catalog(
        quadlet_toml=args.quadlet_toml,
        quadlet_dir=args.quadlet_dir,
    )
    payload = [asdict(entry) for entry in entries]
    json.dump(payload, sys.stdout, indent=2)
    sys.stdout.write("\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())