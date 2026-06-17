#!/usr/bin/env python3
"""Verify enabled stack.yml quality_gates are wired in pre-commit and/or CI.

Reads .claude/stack.yml (registry), .pre-commit-config.yaml, and
.github/workflows/ci.yml. Each enabled gate must appear in at least one
wiring file unless listed in CI_EXEMPT (host-dependent gates).

Exit 0 = wired. Exit 1 = orphan(s). Exit 2 = script error.

Usage:
    uv run python tools/audit_gate_wiring.py [--root ROOT]
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

import yaml

# Gates that may skip on CI runners but must still appear in wiring files.
# (Empty — add gate IDs here only when host-only and intentionally unwired in CI.)
CI_EXEMPT: frozenset[str] = frozenset()

# Gates without a script: key in stack.yml → substring(s) to find in wiring files.
_EXTRA_MARKERS: dict[str, tuple[str, ...]] = {
    "import_layers": ("lint-imports",),
    "file_length": ("check_file_length",),
    "folder_size": ("check_folder_size",),
}


def _markers(gate_id: str, cfg: dict) -> tuple[str, ...]:
    script = cfg.get("script")
    if script:
        stem = Path(str(script)).stem
        return (stem,)
    extra = _EXTRA_MARKERS.get(gate_id)
    if extra:
        return extra
    return (gate_id.replace("_", "-"), gate_id)


def _load_text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _parse_gates(stack_path: Path) -> dict[str, dict]:
    data = yaml.safe_load(stack_path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        raise ValueError(f"{stack_path}: expected mapping at root")
    qg = data.get("quality_gates")
    if not isinstance(qg, dict):
        raise ValueError(f"{stack_path}: quality_gates block missing or invalid")
    return qg


def audit(root: Path) -> list[str]:
    stack_path = root / ".claude" / "stack.yml"
    pre_commit_path = root / ".pre-commit-config.yaml"
    ci_path = root / ".github" / "workflows" / "ci.yml"

    for p in (stack_path, pre_commit_path, ci_path):
        if not p.is_file():
            raise FileNotFoundError(p)

    gates = _parse_gates(stack_path)
    pre_commit = _load_text(pre_commit_path)
    ci = _load_text(ci_path)

    orphans: list[str] = []
    for gate_id, cfg in sorted(gates.items()):
        if not isinstance(cfg, dict):
            continue
        if cfg.get("enabled") is False:
            continue

        markers = _markers(gate_id, cfg)
        in_pre_commit = any(m in pre_commit for m in markers)
        in_ci = any(m in ci for m in markers)

        if gate_id in CI_EXEMPT:
            if not (in_pre_commit or in_ci):
                orphans.append(
                    f"{gate_id}: CI_EXEMPT but not wired locally — "
                    f"add pre-push hook or document manual run"
                )
            continue

        if not (in_pre_commit or in_ci):
            want = ", ".join(markers)
            orphans.append(
                f"{gate_id}: not found in .pre-commit-config.yaml or ci.yml "
                f"(markers: {want})"
            )

    return orphans


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path.cwd(),
        help="Repository root (default: cwd)",
    )
    args = parser.parse_args(argv)
    root = args.root.resolve()

    try:
        orphans = audit(root)
    except (FileNotFoundError, ValueError, yaml.YAMLError) as exc:
        print(f"audit_gate_wiring: error: {exc}", file=sys.stderr)
        return 2

    if orphans:
        print("audit_gate_wiring: FAILED — orphan quality_gates:", file=sys.stderr)
        for line in orphans:
            print(f"  - {line}", file=sys.stderr)
        return 1

    print("audit_gate_wiring: OK — all enabled stack.yml gates are wired")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())