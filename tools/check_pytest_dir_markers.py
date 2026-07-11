#!/usr/bin/env python3
"""Directory↔marker layout gate for CI pytest partitions.

Ensures infra / integration markers only appear under their allowed directory
prefixes (SSoT: tools/pytest_partitions.py ``MARKER_DIR_ALLOWLIST``).

Exit 0 = OK. Exit 1 = layout drift. Exit 2 = collection error.

Usage:
    PYTHONPATH=src uv run python tools/check_pytest_dir_markers.py
"""

from __future__ import annotations

import importlib.util
import io
import sys
from contextlib import redirect_stderr, redirect_stdout
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent


def _load_partitions():
    path = REPO_ROOT / "tools" / "pytest_partitions.py"
    spec = importlib.util.spec_from_file_location("pytest_partitions", path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load partition SSoT: {path}")
    mod = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = mod
    spec.loader.exec_module(mod)
    return mod


_partitions = _load_partitions()
MARKER_DIR_ALLOWLIST = _partitions.MARKER_DIR_ALLOWLIST
DIR_MARKER_COLLECT_PATHS = _partitions.DIR_MARKER_COLLECT_PATHS


def _rel_path(item_path: Path) -> str:
    try:
        return item_path.resolve().relative_to(REPO_ROOT.resolve()).as_posix()
    except ValueError:
        return item_path.as_posix()


def _collect_marked_paths() -> dict[str, list[str]]:
    """Collect tests and map each allowlisted marker → relative file paths."""
    import pytest

    class _Collector:
        def __init__(self) -> None:
            self.by_marker: dict[str, list[str]] = {
                m: [] for m in MARKER_DIR_ALLOWLIST
            }

        def pytest_collection_modifyitems(
            self,
            items: list[pytest.Item],
        ) -> None:
            for item in items:
                path = getattr(item, "path", None)
                if path is None:
                    # pytest < 7 fallback
                    path = Path(str(item.fspath))
                rel = _rel_path(Path(path))
                names = {m.name for m in item.iter_markers()}
                for marker in MARKER_DIR_ALLOWLIST:
                    if marker in names:
                        self.by_marker[marker].append(rel)

    plugin = _Collector()
    args = [
        "--collect-only",
        "-q",
        "--disable-warnings",
        *DIR_MARKER_COLLECT_PATHS,
    ]
    # Suppress collect-only nodeid dump; keep markers via the plugin.
    sink = io.StringIO()
    with redirect_stdout(sink), redirect_stderr(sink):
        code = pytest.main(args, plugins=[plugin])
    if code not in (0, 5):  # 5 = no tests collected
        detail = sink.getvalue().strip()
        raise RuntimeError(
            f"pytest collect failed (exit {code})"
            + (f":\n{detail[-2000:]}" if detail else "")
        )
    # dedupe paths per marker
    return {
        m: sorted(set(paths)) for m, paths in plugin.by_marker.items()
    }


def _check_allowlist(
    marker: str,
    paths: list[str],
    prefixes: tuple[str, ...],
) -> list[str]:
    errors: list[str] = []
    for path in paths:
        if not any(path.startswith(p) for p in prefixes):
            errors.append(
                f"{marker}: {path} not under allowed prefixes {list(prefixes)}"
            )
    return errors


def main() -> int:
    try:
        by_marker = _collect_marked_paths()
    except RuntimeError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    errors: list[str] = []
    counts: list[str] = []
    for marker, prefixes in MARKER_DIR_ALLOWLIST.items():
        paths = by_marker.get(marker, [])
        counts.append(f"{marker}={len(paths)}")
        errors.extend(_check_allowlist(marker, paths, prefixes))

    print("check_pytest_dir_markers:", ", ".join(counts))
    if errors:
        for err in errors:
            print(f"FAIL: {err}", file=sys.stderr)
        return 1
    print("check_pytest_dir_markers: OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
